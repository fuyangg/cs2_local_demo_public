import json
from pathlib import Path
import subprocess

import pytest

from cs2ta import release


def git(root, *args):
    return subprocess.check_output(["git", *args], cwd=root, text=True).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "dev"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Release test")
    git(root, "config", "user.email", "test@example.invalid")
    (root / ".gitignore").write_text(".runtime/\n.release/\n.venv/\ndata/\n.cache/\n")
    (root / "uv.lock").write_text("fixture-lock")
    (root / "app.py").write_text("# fixture version one\n")
    git(root, "add", ".")
    git(root, "commit", "-m", "Initial main")
    return root


@pytest.fixture
def fake_uv(monkeypatch):
    original = release.run
    commands = []

    def execute(root, *args, capture=True):
        if args[0] != "uv":
            return original(root, *args, capture=capture)
        commands.append(args)
        for arg in args:
            if arg.startswith("--junitxml="):
                Path(arg.split("=", 1)[1]).write_text('<testsuites><testsuite tests="5" failures="0" errors="0" skipped="0"/></testsuites>')
        return ""

    monkeypatch.setattr(release, "run", execute)
    return commands


def approved_tag(root, version):
    receipt = release.prepare(root, version)
    release.approve(root, version, receipt["commit"], "test reviewer", "Fixture preview confirmed", True)
    release.publish(root, version)
    return receipt


def test_publish_requires_real_test_report_and_explicit_content_confirmation(repository, fake_uv):
    receipt = release.prepare(repository, "v0.2.0")
    with pytest.raises(ValueError, match="人工内容确认"):
        release.publish(repository, "v0.2.0")
    assert not git(repository, "tag", "--list")
    with pytest.raises(ValueError, match="确认预览"):
        release.approve(repository, "v0.2.0", receipt["commit"], "reviewer", "checked", False)
    release.approve(repository, "v0.2.0", receipt["commit"], "reviewer", "checked playback", True)
    release.publish(repository, "v0.2.0")
    commit, evidence = release.validated_tag(repository, "v0.2.0")
    assert commit == receipt["commit"]
    assert evidence["manual_review"]["reviewer"] == "reviewer"
    assert evidence["tests"]["tests"] == 5


def test_candidate_cannot_be_approved_after_code_changes(repository, fake_uv):
    receipt = release.prepare(repository, "v0.2.0")
    (repository / "app.py").write_text("# changed after preview\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-m", "New main commit")
    with pytest.raises(ValueError, match="SHA 不一致"):
        release.approve(repository, "v0.2.0", receipt["commit"], "reviewer", "checked", True)


def test_main_ancestry_and_clean_tree_are_required(repository):
    git(repository, "switch", "-c", "feature/unmerged")
    (repository / "app.py").write_text("# feature\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-m", "Unmerged feature")
    with pytest.raises(ValueError, match="进入 main"):
        release.main_commit(repository)
    (repository / "app.py").write_text("# dirty\n")
    with pytest.raises(ValueError, match="未提交"):
        release.main_commit(repository)


def test_skipped_tests_and_modified_evidence_do_not_qualify(repository, fake_uv):
    receipt = release.prepare(repository, "v0.2.0")
    report = repository / receipt["report"]
    report.write_text('<testsuites><testsuite tests="5" skipped="1"/></testsuites>')
    with pytest.raises(ValueError, match="跳过项"):
        release.test_summary(report)
    with pytest.raises(ValueError, match="测试证据已改变"):
        release.read_candidate(repository, "v0.2.0")


def test_failed_candidate_rerun_invalidates_an_old_approval(repository, fake_uv, monkeypatch):
    receipt = release.prepare(repository, "v0.2.0")
    release.approve(repository, "v0.2.0", receipt["commit"], "reviewer", "previous check", True)
    original = release.run

    def fail(root, *args, **kwargs):
        if args[:2] == ("uv", "sync"):
            raise ValueError("fixture preparation failed")
        return original(root, *args, **kwargs)

    monkeypatch.setattr(release, "run", fail)
    with pytest.raises(ValueError, match="preparation failed"):
        release.prepare(repository, "v0.2.0")
    assert not release.candidate_path(repository, "v0.2.0").exists()


def test_lightweight_or_unapproved_tags_cannot_enter_production(repository):
    git(repository, "tag", "v0.2.0")
    with pytest.raises(ValueError, match="附注标签"):
        release.validated_tag(repository, "v0.2.0")
    git(repository, "tag", "-a", "v0.2.1", "-m", '{}')
    with pytest.raises(ValueError, match="候选发布记录"):
        release.validated_tag(repository, "v0.2.1")
    with pytest.raises(ValueError, match="尚未成功安装"):
        release.production_ready(repository)


def test_prod_is_a_pinned_worktree_with_isolated_environment_and_rollback(repository, fake_uv):
    first = approved_tag(repository, "v0.2.0")
    prod = repository.parent / "prod"
    release.install(repository, "v0.2.0", prod, fetch=False)
    assert release.production_ready(prod)["version"] == "v0.2.0"
    with pytest.raises(ValueError, match="dev 目录"):
        release.main_commit(prod)
    assert git(prod, "branch", "--show-current") == ""
    assert any(args == ("uv", "sync", "--locked", "--no-dev") for args in fake_uv)
    data = prod / "data/prod"
    data.mkdir(parents=True)
    (data / "keep.json").write_text("retained")
    (repository / "app.py").write_text("# version two\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-m", "Version two")
    approved_tag(repository, "v0.2.1")
    assert git(prod, "rev-parse", "HEAD") == first["commit"]  # main never updates prod implicitly.
    release.install(repository, "v0.2.1", prod, fetch=False)
    assert release.production_ready(prod)["previous_version"] == "v0.2.0"
    release.install(repository, "v0.2.0", prod, fetch=False)
    assert release.production_ready(prod)["version"] == "v0.2.0"
    assert (data / "keep.json").read_text() == "retained"


def test_running_or_dirty_prod_is_not_overwritten(repository, fake_uv):
    approved_tag(repository, "v0.2.0")
    prod = repository.parent / "prod"
    release.install(repository, "v0.2.0", prod, fetch=False)
    with release.runtime_lock(prod):
        with pytest.raises(ValueError, match="正在运行"):
            release.install(repository, "v0.2.0", prod, fetch=False)
    (prod / "app.py").write_text("# manual production changes\n")
    with pytest.raises(ValueError, match="未提交"):
        release.install(repository, "v0.2.0", prod, fetch=False)
    assert (prod / "app.py").read_text() == "# manual production changes\n"


def test_failed_install_keeps_prod_stopped_and_preserves_the_previous_marker(repository, fake_uv, monkeypatch):
    first = approved_tag(repository, "v0.2.0")
    prod = repository.parent / "prod"
    release.install(repository, "v0.2.0", prod, fetch=False)
    (repository / "app.py").write_text("# version two\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-m", "Version two")
    approved_tag(repository, "v0.2.1")
    original = release.run

    def fail_sync(root, *args, **kwargs):
        if args[:2] == ("uv", "sync"):
            raise ValueError("fixture sync failure")
        return original(root, *args, **kwargs)

    monkeypatch.setattr(release, "run", fail_sync)
    with pytest.raises(ValueError, match="安装失败"):
        release.install(repository, "v0.2.1", prod, fetch=False)
    assert git(prod, "rev-parse", "HEAD") == first["commit"]
    with pytest.raises(ValueError, match="尚未成功安装"):
        release.production_ready(prod)
    marker = json.loads((prod / ".runtime/deployment.json").read_text())
    assert marker["version"] == "v0.2.0"
