"""Use real HTTP upload and Streamlit rendering with synthetic parser facts."""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

from playwright.sync_api import expect


def test_upload_analysis_round_playback_rename_and_failed_uploads(page, tmp_path, visual_evidence):
    project = Path(__file__).resolve().parents[2]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    bootstrap = tmp_path / "serve.py"
    bootstrap.write_text('''
import sys
from pathlib import Path
project = Path(sys.argv[1])
port = sys.argv[2]
sys.path.insert(0, str(project / "tests"))
from test_backend import FixtureParser
from cs2ta import pipeline
class UploadParser(FixtureParser):
    def parse_events(self, wanted, other):
        if Path(self.path).read_bytes().endswith(b"broken"):
            raise ValueError("测试录像无法解析")
        return super().parse_events(wanted, other)
pipeline.DemoParser = UploadParser
sys.argv = ["streamlit", "run", str(project / "app.py"), "--server.address", "127.0.0.1",
            "--server.port", port, "--server.headless", "true", "--browser.gatherUsageStats", "false"]
from streamlit.web import cli
cli.main()
''')
    log = tmp_path / "server.log"
    with log.open("w") as output:
        process = subprocess.Popen([sys.executable, str(bootstrap), str(project), str(port)], cwd=tmp_path,
                                   env=dict(os.environ, CS2TA_ENV="dev"), stdout=output, stderr=subprocess.STDOUT)
    try:
        address = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + 25
        while True:
            try:
                with urlopen(address + "/_stcore/health", timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise AssertionError(log.read_text())
                time.sleep(.1)
        page.goto(address)
        expect(page.get_by_role("button", name="上传并分析", exact=True)).to_be_disabled()
        assert not page.get_by_text("本地 Demo 文件路径", exact=True).count()
        transfers = []
        page.on("request", lambda request: transfers.append(request.url) if request.method == "PUT" else None)

        def analyze(name, content):
            page.locator('input[type="file"]').set_input_files(
                {"name": name, "mimeType": "application/octet-stream", "buffer": content})
            page.get_by_role("button", name="上传并分析", exact=True).click()

        analyze("match.dem", b"PBDEMS2\x00valid")
        expect(page.get_by_text("已载入：match.dem", exact=True)).to_be_visible(timeout=30000)
        assert any("/_stcore/upload_file/" in url for url in transfers)
        assert len(list((tmp_path / "data/dev/uploads").glob("*.dem"))) == 1
        assert len(list((tmp_path / "data/dev").glob("*/manifest.json"))) == 1
        source = next((tmp_path / "data/dev/uploads").glob("*.dem"))
        assert source.read_bytes() == b"PBDEMS2\x00valid"
        page.get_by_text("高级设置（通常保持默认即可）", exact=True).click()
        page.get_by_test_id("stSelectbox").filter(has=page.get_by_text("地图显示", exact=True)).get_by_role("combobox").click()
        page.get_by_role("option", name="世界坐标", exact=True).click()
        page.get_by_role("button", name="查看这 1 个回合", exact=True).click()
        canvas = page.frame_locator("iframe").locator("canvas")
        expect(canvas).to_have_attribute("data-visible-markers", "10", timeout=20000)
        visual_evidence.source = {"kind": "synthetic", "description": "真实 HTTP 上传与页面分析；比赛事实来自合成解析器，回合位置从服务器保存的上传副本读取。"}
        visual_evidence.snapshot("uploaded-match", "上传后完成分析并加载回合轨迹，不填写服务器文件路径")
        manifest = next((tmp_path / "data/dev").glob("*/manifest.json"))
        before = manifest.read_bytes()
        analyze("renamed.dem", b"PBDEMS2\x00valid")
        expect(page.get_by_text("已载入：renamed.dem", exact=True)).to_be_visible(timeout=30000)
        assert manifest.read_bytes() == before
        assert len(list((tmp_path / "data/dev/uploads").glob("*.dem"))) == 1
        analyze("invalid.dem", b"not a demo")
        expect(page.get_by_text("上传分析失败：文件头不是 CS2 录像。请确认已解压，且不是 CS:GO 录像。", exact=True)).to_be_visible()
        assert not page.get_by_text("已载入：renamed.dem", exact=True).count()
        analyze("broken.dem", b"PBDEMS2\x00broken")
        expect(page.get_by_text("上传分析失败：测试录像无法解析", exact=True)).to_be_visible()
        assert source.is_file()
        assert len(list((tmp_path / "data/dev/uploads").glob("*.dem"))) == 1
        assert not list((tmp_path / "data/dev/uploads").glob("*.part"))
        assert not page.get_by_test_id("stException").count()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
