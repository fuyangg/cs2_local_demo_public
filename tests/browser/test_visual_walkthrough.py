"""A short, asserted walkthrough with optional screenshots and recordings."""

import json
import math
from pathlib import Path

import polars as pl
from PIL import Image
import pytest

pytest.importorskip("playwright.sync_api")

from cs2ta.pipeline import load_dataset, position_cache_path
from cs2ta.playback import playback_html, playback_payload
from cs2ta.radar import ASSET_VERSION, RADARS, radar_path


def synthetic_scene():
    rounds = pl.DataFrame([
        {"round_id": rid, "start_tick": anchor, "freeze_end_tick": anchor,
         "end_tick": anchor + duration, "team_a_side": 2, "team_b_side": 3,
         "team_a_name": "G2（测试）", "team_b_name": "NAVI（测试）"}
        for rid, anchor, duration in [(1, 1000, 2048), (2, 5000, 1536)]], strict=False)
    states = {}
    for row in rounds.iter_rows(named=True):
        states[row["round_id"]] = pl.DataFrame([
            {"tick": row["start_tick"] + tick, "steamid": f"{side}{index}",
             "name": f"Test {side}-{index}", "team_num": side, "player_color_id": index,
             "x": -2000 + index * 120 + tick * .7 + (side - 2) * 250,
             "y": 1000 + index * 140 + math.sin(tick / 400 + index) * 200 + row["round_id"] * 100,
             "z": 0., "health": 100, "is_alive": True}
            for tick in range(0, int(row["end_tick"] - row["start_tick"]) + 1, 16)
            for side in (2, 3) for index in range(5)], strict=False)
    tracks = pl.DataFrame([
        {"tick": tick, "grenade_entity_id": 7, "grenade_type": "CSmokeGrenadeProjectile",
         "steamid": "20", "name": "Test 2-0", "x": -1900. + (tick - 1064) * 8,
         "y": 1300., "z": 0.} for tick in range(1064, 1097)], strict=False)
    events = pl.DataFrame([{"tick": 1096, "event_type": "smokegrenade_detonate", "entityid": 7,
                            "user_steamid": "20", "x": -1644., "y": 1300., "z": 0.}], strict=False)
    return states, rounds, tracks, events


@pytest.fixture
def scene(request, visual_evidence):
    dataset = request.config.getoption("--visual-dataset")
    spec, images = None, {}
    if dataset:
        destination = Path(dataset).expanduser().resolve()
        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        events, rounds = load_dataset(destination)
        paths = {rid: position_cache_path(destination, rid, 16) for rid in (1, 2)}
        utility_path = destination / "utility-tracks-v1.parquet"
        if any(not path.is_file() for path in paths.values()) or not utility_path.is_file():
            pytest.fail("真实演示需要已准备的第 1、2 回合 step16 位置缓存及道具缓存。")
        states = {rid: pl.read_parquet(path) for rid, path in paths.items()}
        tracks = pl.read_parquet(utility_path)
        visual_evidence.source = {
            "kind": "demo", "description": f"真实 Demo · {manifest['header'].get('map_name')} · 前两回合",
            "demo_sha256": manifest["demo_sha256"], "parser_version": manifest["parser_version"],
            "schema_version": manifest["schema_version"], "round_ids": [1, 2]}
        cache = request.config.getoption("--visual-map-cache")
        if cache:
            spec = RADARS.get(manifest["header"].get("map_name"))
            if spec:
                layers = ["upper", "lower"] if spec.lower_max_z is not None else ["upper"]
                images = {layer: radar_path(spec, layer, Path(cache)) for layer in layers}
                if any(not path.is_file() for path in images.values()):
                    pytest.fail("指定地图缓存不完整；测试不会自动联网下载。")
                visual_evidence.source["map_asset_version"] = ASSET_VERSION
    else:
        states, rounds, tracks, events = synthetic_scene()
    return playback_payload(states, rounds, 16, spec, images,
                            utility_tracks=tracks, events=events)


def seek(page, tick):
    page.locator("#timeline").evaluate("(el,t)=>{el.value=t;el.dispatchEvent(new Event('input'))}", tick)
    page.wait_for_function("tick=>Number(document.querySelector('canvas').dataset.renderTick)===tick", arg=tick)


def test_player_visual_walkthrough(page, scene, visual_evidence):
    page.set_content(playback_html(scene))
    page.wait_for_function("Number(document.querySelector('canvas').dataset.drawCount)>0")
    if scene["images"]:
        page.wait_for_function("Object.values(images).every(image=>image.complete&&image.naturalWidth>0)")
    assert len(scene["rounds"]) == 2
    assert page.locator("input[data-round]:checked").count() == 2
    if not page.locator("#roundPanel").evaluate("panel=>panel.open"):
        page.locator("#roundPanel > summary").click()
    page.locator("#mapSize").select_option("400")
    seek(page, min(1600, scene["maxTick"]))
    assert int(page.locator("canvas").get_attribute("data-visible-markers")) > 0
    visual_evidence.snapshot("01-round-overlay", "两个回合对齐叠加：地图、轨迹与时间轴")

    seek(page, 0)
    page.locator("#speed").select_option("8")
    page.locator("#play").click()
    page.wait_for_function("Number(document.querySelector('canvas').dataset.renderTick)>0")
    with visual_evidence.animation("02-playback", "实际播放录制：8× 速度，观察轨迹与时间轴变化") as clip:
        clip.playback(seconds=3, fps=6)
    if page.locator("#play").get_attribute("aria-pressed") == "true":
        page.locator("#play").click()
    assert page.locator("#play").get_attribute("aria-pressed") == "false"
    if visual_evidence.enabled:
        assert len({sample["tick"] for sample in clip.samples}) > 1

    # These frames each follow a verified interaction; GIF duration is explanatory.
    seek(page, 96)
    page.evaluate("window.scrollTo(0,0)")
    with visual_evidence.animation("03-filters", "操作检查点：全员 → 单人 → 单回合 → 道具总览") as clip:
        clip.capture(800)
        page.locator("[data-solo]").first.click()
        assert page.locator("input[data-player]:checked").count() == 1
        clip.capture(1000)
        visual_evidence.snapshot("04-player-solo", "单人筛选：跨两个回合观察同一玩家")
        page.locator('input[data-round="2"]').uncheck()
        assert page.locator("input[data-round]:checked").count() == 1
        clip.capture(1000)
        page.locator("#allPlayers").click()
        page.locator('input[data-round="2"]').check()
        page.locator("#utilityMode").select_option("overview")
        page.evaluate("window.scrollTo(0,0)")
        page.wait_for_function("Number(document.querySelector('canvas').dataset.visibleUtilities)>0")
        assert page.locator("#utilityMode").input_value() == "overview"
        clip.capture(1200)
        visual_evidence.snapshot("05-utility-overview", "道具总览：实测轨迹、落点与范围示意")

    if visual_evidence.enabled:
        files = list(visual_evidence.directory.glob("*"))
        assert len(files) == 5
        for path in files:
            with Image.open(path) as image:
                assert image.width <= 960
                if path.suffix == ".gif":
                    assert image.n_frames > 1
