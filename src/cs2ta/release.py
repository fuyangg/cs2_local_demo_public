"""Two-stage reviewed releases and non-destructive local production worktrees."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

VERSION = re.compile(r"v(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")


def run(root: Path, *args: str, capture=True):
    result = subprocess.run(args, cwd=root, text=True, capture_output=capture)
    if result.returncode:
        raise ValueError(f"命令失败：{' '.join(args)}\n{result.stderr or ''}")
    return result.stdout.strip() if capture else ""


def git(root: Path, *args: str) -> str:
    return run(root, "git", *args)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        temporary = Path(stream.name)
    temporary.replace(path)


def check_version(version: str) -> None:
    if not VERSION.fullmatch(version):
        raise ValueError("发布版本需为 v主版本.次版本.修订号，例如 v0.2.0。")


def clean(root: Path) -> None:
    if git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("代码目录有未提交改动；请先提交或移到开发分支。不会自动清理或覆盖文件。")


def main_commit(root: Path, expected: str | None = None) -> str:
    if (root / ".runtime/deployment.json").is_file():
        raise ValueError("发布候选和审批必须在 dev 目录执行，不能修改稳定版环境。")
    clean(root)
    commit = git(root, "rev-parse", "HEAD")
    if expected is not None and (not re.fullmatch(r"[0-9a-f]{40}", expected) or commit != expected):
        raise ValueError("候选提交与指定的完整 commit SHA 不一致。")
    reference = "refs/remotes/origin/main"
    if subprocess.run(["git", "show-ref", "--verify", "--quiet", reference], cwd=root).returncode:
        reference = "refs/heads/main"
    if subprocess.run(["git", "merge-base", "--is-ancestor", commit, reference], cwd=root).returncode:
        raise ValueError("候选提交尚未进入 main，请先完成 PR 和测试后合入。")
    return commit


def test_summary(report: Path) -> dict:
    tree = ET.parse(report).getroot()
    suites = [tree] if tree.tag == "testsuite" else tree.findall(".//testsuite")
    summary = {key: sum(int(suite.get(key, "0")) for suite in suites)
               for key in ["tests", "failures", "errors", "skipped"]}
    if summary["tests"] <= 0 or any(summary[key] for key in ["failures", "errors", "skipped"]):
        raise ValueError("发布要求全部测试通过且没有跳过项，包括真实浏览器测试。")
    return summary


def candidate_path(root: Path, version: str) -> Path:
    check_version(version)
    return root / ".release" / f"candidate-{version}.json"


def prepare(root: Path, version: str, expected: str | None = None) -> dict:
    check_version(version)
    commit = main_commit(root, expected)
    if git(root, "tag", "--list", version):
        raise ValueError("该版本标签已存在，请使用新版本号。")
    # A failed rerun must not leave an older approval usable for this candidate.
    candidate_path(root, version).unlink(missing_ok=True)
    report = root / ".release" / f"tests-{version}.xml"
    report.parent.mkdir(parents=True, exist_ok=True)
    run(root, "uv", "sync", "--locked", "--group", "browser", capture=False)
    if not (shutil.which("chromium") or shutil.which("google-chrome")):
        run(root, "uv", "run", "--locked", "--group", "browser", "playwright", "install", "chromium", capture=False)
    report.unlink(missing_ok=True)
    run(root, "uv", "run", "--locked", "--group", "browser", "pytest", "-q", f"--junitxml={report}", capture=False)
    summary = test_summary(report)
    main_commit(root, commit)  # Testing must not alter the reviewed source tree.
    receipt = {"kind": "cs2ta-approved-release", "version": version, "commit": commit,
               "lock_sha256": sha256(root / "uv.lock"), "tests": summary, "tested_at": now(),
               "report": str(report.relative_to(root)), "report_sha256": sha256(report), "manual_review": None}
    write_json(candidate_path(root, version), receipt)
    print(f"候选测试通过：{version}\n完整提交：{commit}\n请在 dev 中确认该版本的界面和功能，再执行 approve。")
    return receipt


def read_candidate(root: Path, version: str) -> dict:
    receipt = json.loads(candidate_path(root, version).read_text(encoding="utf-8"))
    main_commit(root, receipt["commit"])
    report = (root / receipt["report"]).resolve()
    if not report.is_relative_to((root / ".release").resolve()):
        raise ValueError("测试报告路径不在候选目录中。")
    if sha256(report) != receipt["report_sha256"] or test_summary(report) != receipt["tests"]:
        raise ValueError("测试证据已改变，请重新 prepare。")
    if sha256(root / "uv.lock") != receipt["lock_sha256"] or receipt["version"] != version:
        raise ValueError("候选版本或依赖锁发生变化，请重新 prepare。")
    return receipt


def approve(root: Path, version: str, commit: str, reviewer: str, notes: str, confirmed: bool) -> dict:
    receipt = read_candidate(root, version)
    if not confirmed or receipt["commit"] != commit or not reviewer.strip() or not notes.strip():
        raise ValueError("需确认预览，并提供一致的完整 commit SHA、确认人和确认说明。")
    receipt["manual_review"] = {"reviewer": reviewer.strip(), "notes": notes.strip(),
                                "commit": commit, "confirmed_preview": True, "approved_at": now()}
    write_json(candidate_path(root, version), receipt)
    print(f"已记录人工确认：{version} · {commit}。尚未创建标签或更新 prod。")
    return receipt


def validate_receipt(receipt: dict, version: str, commit: str, lock_hash: str) -> None:
    check_version(version)
    if receipt.get("kind") != "cs2ta-approved-release" or receipt.get("version") != version or receipt.get("commit") != commit:
        raise ValueError("标签缺少有效的候选发布记录，或指向了不同提交。")
    if receipt.get("lock_sha256") != lock_hash:
        raise ValueError("发布依赖锁校验失败。")
    summary = receipt.get("tests", {})
    if not isinstance(summary.get("tests"), int) or summary["tests"] <= 0 or any(summary.get(k) != 0 for k in ["failures", "errors", "skipped"]):
        raise ValueError("发布记录没有完整通过的测试证据。")
    review = receipt.get("manual_review") or {}
    if review.get("confirmed_preview") is not True or review.get("commit") != commit or not review.get("reviewer") or not review.get("notes"):
        raise ValueError("该版本尚未完成人工内容确认。")


def publish(root: Path, version: str, push: bool = False) -> None:
    receipt = read_candidate(root, version)
    validate_receipt(receipt, version, git(root, "rev-parse", "HEAD"), sha256(root / "uv.lock"))
    if git(root, "tag", "--list", version):
        raise ValueError("已存在同名标签，不会覆盖或强推。")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as body:
        json.dump(receipt, body, ensure_ascii=False, indent=2)
        body.flush()
        git(root, "tag", "--annotate", version, "--file", body.name)
    if push:
        run(root, "git", "push", "origin", f"refs/tags/{version}", capture=False)
    print(f"已创建已测试且已人工确认的标签 {version}。prod 尚未自动更新。")


def validated_tag(root: Path, version: str) -> tuple[str, dict]:
    check_version(version)
    reference = f"refs/tags/{version}"
    if git(root, "cat-file", "-t", reference) != "tag":
        raise ValueError("prod 仅接受含确认记录的附注标签，不接受分支或轻量标签。")
    annotation = git(root, "cat-file", "-p", reference).split("\n\n", 1)[1]
    receipt = json.loads(annotation)
    commit = git(root, "rev-parse", f"{reference}^{{commit}}")
    lock_bytes = subprocess.check_output(["git", "show", f"{commit}:uv.lock"], cwd=root)
    validate_receipt(receipt, version, commit, hashlib.sha256(lock_bytes).hexdigest())
    return commit, receipt


@contextmanager
def runtime_lock(root: Path):
    path = root / ".runtime/server.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("此环境正在运行或更新，请先停止其服务后重试。") from error
        try:
            yield stream
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def production_ready(root: Path) -> dict:
    clean(root)
    marker = root / ".runtime/deployment.json"
    if not marker.is_file() or (root / ".runtime/deployment-failed.json").exists():
        raise ValueError("prod 尚未成功安装已批准版本，请通过 release install 部署。")
    deployment = json.loads(marker.read_text(encoding="utf-8"))
    commit, _ = validated_tag(root, deployment["version"])
    if commit != git(root, "rev-parse", "HEAD") or commit != deployment["commit"]:
        raise ValueError("prod 代码与已部署标签不一致，拒绝启动。")
    if git(root, "branch", "--show-current"):
        raise ValueError("prod 必须固定在发布标签的 detached HEAD，不能跟随 main。")
    return deployment


def install(root: Path, version: str, destination: Path, fetch=True) -> dict:
    if (root / ".runtime/deployment.json").is_file():
        raise ValueError("请从 dev 目录管理 prod 安装或回退。")
    destination = destination.expanduser().resolve()
    if destination == root.resolve() or destination.is_relative_to(root.resolve()) or root.resolve().is_relative_to(destination):
        raise ValueError("prod 必须是独立的同级目录，不能覆盖或嵌套 dev。")
    if fetch:
        run(root, "git", "fetch", "origin", "--tags", capture=False)
    commit, _ = validated_tag(root, version)
    created = not destination.exists()
    if created:
        run(root, "git", "worktree", "add", "--detach", str(destination), f"refs/tags/{version}", capture=False)
    else:
        if git(destination, "rev-parse", "--path-format=absolute", "--git-common-dir") != git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"):
            raise ValueError("目标不是此仓库的工作树，不会修改。")
    with runtime_lock(destination):
        clean(destination)
        old_head = git(destination, "rev-parse", "HEAD")
        marker = destination / ".runtime/deployment.json"
        previous = json.loads(marker.read_text(encoding="utf-8")) if marker.is_file() else {}
        try:
            run(destination, "git", "switch", "--detach", f"refs/tags/{version}", capture=False)
            run(destination, "uv", "sync", "--locked", "--no-dev", capture=False)
        except Exception as error:
            if not created:
                run(destination, "git", "switch", "--detach", old_head, capture=False)
            write_json(destination / ".runtime/deployment-failed.json", {"error": str(error), "at": now()})
            raise ValueError("prod 安装失败，尚未标记为可运行。已有代码已尽量恢复，请重试安装已知版本以恢复依赖。") from error
        previous_version = previous.get("version") if previous.get("version") != version else previous.get("previous_version")
        deployment = {"version": version, "commit": commit, "previous_version": previous_version, "installed_at": now()}
        write_json(marker, deployment)
        (destination / ".runtime/deployment-failed.json").unlink(missing_ok=True)
        history = destination / ".runtime/deployments.json"
        records = json.loads(history.read_text(encoding="utf-8")) if history.is_file() else {"deployments": []}
        records["deployments"].append(deployment)
        write_json(history, records)
    print(f"prod 已固定到 {version} · {commit}\n目录：{destination}\n启动：在该目录执行 bash scripts/run-prod.sh")
    return deployment


def main() -> int:
    parser = argparse.ArgumentParser(description="开发候选、人工确认、版本标签、prod 安装及回退")
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare"); p.add_argument("version"); p.add_argument("--expected-commit")
    p = sub.add_parser("approve"); p.add_argument("version"); p.add_argument("--commit", required=True)
    p.add_argument("--reviewer", required=True); p.add_argument("--notes", required=True)
    p.add_argument("--confirm-preview", action="store_true")
    p = sub.add_parser("publish"); p.add_argument("version"); p.add_argument("--push", action="store_true")
    p = sub.add_parser("install"); p.add_argument("version"); p.add_argument("--prod-dir", type=Path)
    p = sub.add_parser("rollback"); p.add_argument("--prod-dir", type=Path)
    args = parser.parse_args()
    root = Path.cwd().resolve()
    destination = getattr(args, "prod_dir", None) or root.parent / f"{root.name}-prod"
    try:
        if args.action == "prepare": prepare(root, args.version, args.expected_commit)
        elif args.action == "approve": approve(root, args.version, args.commit, args.reviewer, args.notes, args.confirm_preview)
        elif args.action == "publish": publish(root, args.version, args.push)
        elif args.action == "install": install(root, args.version, destination)
        else:
            marker = json.loads((destination / ".runtime/deployment.json").read_text(encoding="utf-8"))
            if not marker.get("previous_version"):
                raise ValueError("没有上一个已部署版本，无法自动回退。")
            install(root, marker["previous_version"], destination)
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError, ET.ParseError, subprocess.SubprocessError) as error:
        print(f"发布操作失败：{error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
