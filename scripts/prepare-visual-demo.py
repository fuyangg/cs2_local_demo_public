"""Prepare the pinned public sample for an explicitly requested visual run."""

import argparse
import hashlib
import os
from pathlib import Path
import tempfile
import urllib.request

from cs2ta.backend import development_settings
from cs2ta.pipeline import fingerprint, ingest, parse_rounds_positions, parse_utility_tracks
from cs2ta.radar import download_radars

URL = "https://raw.githubusercontent.com/LaihoE/demoparser/45ca85aeac8fb0de9d385124d2c7fe0e7b8ff0c8/src/parser/test_demo.dem"
CHECKSUM = "84a1a4191302bdd2a3bbb5a727842093744b1fb1a228aeec630369e44b622cb2"


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "public-mirage.dem"
    if not source.is_file() or fingerprint(source) != CHECKSUM:
        fd, temporary = tempfile.mkstemp(dir=root, suffix=".download")
        try:
            checksum, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as output, urllib.request.urlopen(URL, timeout=60) as response:
                while chunk := response.read(8 * 1024 * 1024):
                    size += len(chunk)
                    if size > 128 * 1024 * 1024:
                        raise ValueError("公开测试文件超过允许大小。")
                    checksum.update(chunk)
                    output.write(chunk)
            if checksum.hexdigest() != CHECKSUM:
                raise ValueError("公开 Demo SHA-256 不匹配，停止生成证据。")
            os.replace(temporary, source)
        finally:
            Path(temporary).unlink(missing_ok=True)
    destination, _ = ingest(source, root / "datasets")
    parse_rounds_positions(source, destination, [1, 2], tick_step=16)
    parse_utility_tracks(source, destination)
    maps = root / "maps"
    download_radars(maps)
    return destination, maps


def main():
    parser = argparse.ArgumentParser(description="下载并校验固定公开 Demo，准备真实浏览器演示缓存。")
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/visual-input"))
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    try:
        development_settings()
    except ValueError as error:
        parser.error(str(error))
    dataset, maps = prepare(args.work_dir.expanduser().resolve())
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"dataset={dataset}\nmap_cache={maps}\n")
    print(f"Dataset: {dataset}\nMaps: {maps}")


if __name__ == "__main__":
    main()
