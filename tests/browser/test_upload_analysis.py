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
    def parse_header(self):
        return {"map_name": "de_nuke" if Path(self.path).read_bytes().endswith(b"nuke") else "de_dust2"}
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
        assert page.locator('input[type="file"]').get_attribute("multiple") is not None
        assert page.locator('input[type="file"]').get_attribute("accept") in (None, "")
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
        page.get_by_text("省内存模式（适合 Streamlit Cloud）", exact=True).click()
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
        expect(page.get_by_text("invalid.dem · 上传分析失败：文件头不是 CS2 录像。请确认已解压，且不是 CS:GO 录像。", exact=True)).to_be_visible()
        expect(page.get_by_text("已载入：renamed.dem", exact=True)).to_be_visible()
        # The picker is unrestricted for iOS, but the server must still reject other extensions.
        analyze("not-a-demo.txt", b"PBDEMS2\x00valid header, wrong extension")
        expect(page.get_by_text("not-a-demo.txt · 上传分析失败：请上传已解压的 CS2 .dem 录像。", exact=True)).to_be_visible()
        expect(page.get_by_text("已载入：renamed.dem", exact=True)).to_be_visible()
        analyze("broken.dem", b"PBDEMS2\x00broken")
        expect(page.get_by_text("broken.dem · 上传分析失败：测试录像无法解析", exact=True)).to_be_visible()
        assert source.is_file()
        assert len(list((tmp_path / "data/dev/uploads").glob("*.dem"))) == 1
        assert not list((tmp_path / "data/dev/uploads").glob("*.part"))
        assert not page.get_by_test_id("stException").count()
        # Upload two more files in ONE browser selection. All three distinct matches must remain.
        page.locator('input[type="file"]').set_input_files([
            {"name": "second.dem", "mimeType": "application/octet-stream", "buffer": b"PBDEMS2\x00second"},
            {"name": "third.dem", "mimeType": "application/octet-stream", "buffer": b"PBDEMS2\x00third"}])
        page.get_by_role("button", name="上传并分析", exact=True).click()
        expect(page.get_by_text("本次会话已保留 3 场比赛。移除待上传文件不会删除已分析的比赛。", exact=True)).to_be_visible(timeout=30000)
        expect(page.get_by_text("已选择 3 场比赛；回合、玩家和道具按比赛分别标识。", exact=True)).to_be_visible()
        assert not page.get_by_text("Only one file is allowed.", exact=True).count()
        assert len(list((tmp_path / "data/dev/uploads").glob("*.dem"))) == 3
        page.get_by_role("button", name="勾选筛选结果", exact=True).click()
        page.get_by_role("button", name="查看这 6 个回合", exact=True).click()
        canvas = page.frame_locator("iframe").locator("canvas")
        expect(canvas).to_have_attribute("data-visible-markers", "60", timeout=30000)
        player = page.frame_locator("iframe")
        assert player.locator("#rounds input").count() == 6
        assert player.locator("#players input").count() == 30
        player.locator("#roundPanel > summary").click()
        assert "second.dem" in player.locator("#rounds").inner_text()
        assert "third.dem" in player.locator("#rounds").inner_text()
        player.locator("#rounds input").first.uncheck()
        expect(canvas).to_have_attribute("data-visible-markers", "50")
        player.locator("#players input").first.uncheck()
        expect(canvas).to_have_attribute("data-visible-markers", "49")
        player.locator("#players input").first.check()
        player.locator("#timeline").fill("16")
        player.locator("#timeline").dispatch_event("input")
        expect(canvas).to_have_attribute("data-render-tick", "16")
        with page.expect_popup() as popup_info:
            player.locator("#openWindow").click()
        popup = popup_info.value
        expect(popup.locator("canvas")).to_have_attribute("data-render-tick", "16")
        expect(popup.locator("canvas")).to_have_attribute("data-visible-markers", "50")
        popup.close()
        visual_evidence.snapshot("multi-upload-comparison", "单次选两份并保留旧比赛，三场同地图六回合可独立叠加与筛选")
        analyze("nuke.dem", b"PBDEMS2\x00nuke")
        expect(page.get_by_text("本次会话已保留 4 场比赛。移除待上传文件不会删除已分析的比赛。", exact=True)).to_be_visible(timeout=30000)
        map_picker = page.get_by_test_id("stSelectbox").filter(has=page.get_by_text("地图", exact=True)).get_by_role("combobox")
        map_picker.click()
        page.get_by_role("option", name="de_dust2", exact=True).click()
        expect(page.get_by_text("已选择 3 场比赛；回合、玩家和道具按比赛分别标识。", exact=True)).to_be_visible()
        # Removing every item from the upload queue must not remove analyzed matches.
        remove = page.get_by_test_id("stFileUploaderDeleteBtn")
        while remove.count():
            remove.first.click()
        expect(page.get_by_role("button", name="上传并分析", exact=True)).to_be_disabled()
        expect(page.get_by_text("本次会话已保留 4 场比赛。移除待上传文件不会删除已分析的比赛。", exact=True)).to_be_visible()
        expect(page.get_by_text("已选择 3 场比赛；回合、玩家和道具按比赛分别标识。", exact=True)).to_be_visible()
        # A new browser session must not enumerate this session's server-side uploads.
        other_context = page.context.browser.new_context()
        other = other_context.new_page()
        other.goto(address)
        expect(other.get_by_role("button", name="上传并分析", exact=True)).to_be_disabled()
        assert not other.get_by_text("2. 选择分析比赛", exact=True).count()
        other_context.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
