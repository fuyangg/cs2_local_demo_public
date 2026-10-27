"""Register evidence options before pytest collects browser tests."""

from datetime import datetime, timezone
from pathlib import Path
import os
from uuid import uuid4

import pytest


def pytest_addoption(parser):
    group = parser.getgroup("visual evidence")
    group.addoption("--visual-artifacts", metavar="DIRECTORY", help="保存浏览器 PNG/GIF 与 HTML 索引")
    group.addoption("--visual-dataset", metavar="DIRECTORY", default=os.environ.get("CS2TA_VISUAL_DATASET") or None,
                    help="可选：使用已有真实数据集录制演示")
    group.addoption("--visual-map-cache", metavar="DIRECTORY", default=os.environ.get("CS2TA_VISUAL_MAP_CACHE") or None,
                    help="可选：已有地图缓存根目录（不联网下载）")


def pytest_configure(config):
    requested = config.getoption("--visual-artifacts")
    if config.getoption("--visual-dataset") and not requested:
        raise pytest.UsageError("--visual-dataset 需要同时指定 --visual-artifacts。")
    config._visual_entries = []
    config._visual_root = None
    if requested:
        run_id = datetime.now(timezone.utc).strftime("run-%Y%m%dT%H%M%SZ-") + uuid4().hex[:6]
        config._visual_root = Path(requested).expanduser().resolve() / run_id
        config._visual_root.mkdir(parents=True, exist_ok=True)
