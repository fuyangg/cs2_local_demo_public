"""Check every supported radar in the browser; optionally use downloaded images."""

from pathlib import Path

import polars as pl
from PIL import Image
import pytest

from cs2ta.playback import playback_html, playback_payload
from cs2ta.radar import ASSET_VERSION, RADARS, radar_path


def test_all_active_duty_maps_render_with_projected_player(page, request, tmp_path, visual_evidence):
    cache = request.config.getoption("--visual-map-cache")
    visual_evidence.source = {
        "kind": "synthetic", "map_asset_version": ASSET_VERSION,
        "radar_source": "downloaded" if cache else "synthetic",
        "description": "七张地图的合成轨迹验证；底图为已下载资源。" if cache else "七张地图的合成轨迹和占位底图验证。",
    }
    rounds = pl.DataFrame({"round_id": [1], "start_tick": [0], "freeze_end_tick": [0], "end_tick": [32]})
    with visual_evidence.animation("active-duty-maps", "七张服役地图依次显示：底图加载与玩家投影检查点") as clip:
        for index, (name, spec) in enumerate(RADARS.items()):
            layers = ["upper", "lower"] if spec.lower_max_z is not None else ["upper"]
            images = {}
            for layer in layers:
                path = radar_path(spec, layer, Path(cache)) if cache else tmp_path / f"{name}-{layer}.png"
                if not cache:
                    Image.new("RGB", (1024, 1024), (25 + index * 25, 65, 95)).save(path)
                if not path.is_file():
                    pytest.fail(f"地图缓存缺少 {path}；测试不会联网下载。")
                images[layer] = path
            states = pl.DataFrame([
                {"tick": tick, "steamid": "synthetic-player", "name": "合成投影测试",
                 "x": spec.pos_x + (512 + tick) * spec.scale, "y": spec.pos_y - 512 * spec.scale,
                 "z": -400., "team_num": 2, "is_alive": True, "health": 100, "player_color_id": 0}
                for tick in (0, 16, 32)])
            payload = playback_payload({1: states}, rounds, 16, spec, images)
            assert payload["map"]["name"] == name
            assert payload["rounds"][0]["tracks"][0]["samples"][0][1:3] == [512., 512.]
            page.goto("about:blank")
            page.set_content(playback_html(payload))
            page.wait_for_function("Object.values(images).every(image=>image.complete&&image.naturalWidth===1024)")
            page.wait_for_function("document.querySelector('canvas').dataset.visibleMarkers==='1'")
            assert page.evaluate("DATA.map.name") == name
            page.locator("#timeline").fill("16")
            page.locator("#timeline").dispatch_event("input")
            page.wait_for_function("document.querySelector('canvas').dataset.renderTick==='16'")
            assert page.locator("canvas").get_attribute("data-visible-markers") == "1"
            visual_evidence.snapshot(name, f"{name}：底图与合成玩家投影")
            clip.capture(800)
    if visual_evidence.enabled:
        with Image.open(visual_evidence.directory / "active-duty-maps.gif") as saved:
            assert saved.n_frames == len(RADARS)
