"""Copy a demo into WSL's Linux filesystem without overwriting other data."""

import argparse
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="将 Demo 复制到 WSL 本地目录，并校验 SHA-256")
    parser.add_argument("source", help="源 .dem 路径，例如 /mnt/c/Users/name/Downloads/match.dem")
    parser.add_argument("--destination-dir", default="~/cs2-demos", help="WSL 本地目录，默认 ~/cs2-demos")
    args = parser.parse_args()
    temporary = None
    try:
        source = Path(args.source).expanduser().resolve(strict=True)
        if not source.is_file() or source.suffix.lower() != ".dem":
            raise ValueError("源文件必须为已解压的 .dem 文件。")
        with source.open("rb") as stream:
            if stream.read(8) != b"PBDEMS2\x00":
                raise ValueError("源文件不是 CS2 Demo（PBDEMS2）。")
        directory = Path(args.destination_dir).expanduser().resolve()
        # Reject Windows mount paths, including symlinks resolving into /mnt.
        if directory == Path("/mnt") or Path("/mnt") in directory.parents:
            raise ValueError("目标必须是 WSL Linux 本地目录，例如 ~/cs2-demos，不能位于 /mnt。")
        directory.mkdir(parents=True, exist_ok=True)
        fs_type = subprocess.check_output(
            ["findmnt", "--noheadings", "--output", "FSTYPE", "--target", str(directory)], text=True,
        ).strip().lower()
        if fs_type in {"9p", "drvfs", "cifs", "smb3", "nfs", "nfs4"}:
            raise ValueError(f"目标文件系统 {fs_type} 不是 WSL 本地 Linux 存储。")
        destination = directory / source.name
        before = source.stat()
        print(f"源文件：{source}\n目标文件：{destination}", flush=True)
        if destination.exists():
            if checksum(source) != checksum(destination):
                raise ValueError("目标已有同名但内容不同的文件；未覆盖，请选择其他目标目录。")
            print(f"目标文件已存在且校验一致，无需重复复制。\n页面中使用路径：{destination}")
            return 0
        fd, name = tempfile.mkstemp(prefix=".copy-demo-", suffix=".tmp", dir=directory)
        temporary = Path(name)
        with source.open("rb") as src, os.fdopen(fd, "wb") as dst:
            shutil.copyfileobj(src, dst, length=8 * 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        source_hash = checksum(source)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("复制期间源文件发生变化，请等待下载完成后重试。")
        if source_hash != checksum(temporary):
            raise ValueError("复制校验失败，未发布目标文件。")
        # Atomic no-clobber publication, even when another copy finishes first.
        os.link(temporary, destination)
        print(f"复制完成，SHA-256：{source_hash}\n页面中使用路径：{destination}")
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"复制失败：{error}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
