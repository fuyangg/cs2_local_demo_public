"""Real-browser behavior checks; optional locally, enabled in CI."""

import polars as pl
from PIL import Image
import pytest

playwright = pytest.importorskip("playwright.sync_api")

from cs2ta.playback import playback_html, playback_payload
from cs2ta.radar import RADARS


def open_player(page, tmp_path, nuke=False):
    rows = pl.DataFrame([
        {"round_id": 1, "start_tick": 100, "freeze_end_tick": 100, "end_tick": 420},
        {"round_id": 2, "start_tick": 1000, "freeze_end_tick": 1000, "end_tick": 1160},
    ], strict=False)
    states = {}
    for rid, anchor, duration in [(1, 100, 320), (2, 1000, 160)]:
        states[rid] = pl.DataFrame([
            {"tick": anchor + t, "steamid": pid, "name": name, "x": -2000. + t,
             "y": 1000., "z": z, "health": 100, "is_alive": True, "team_num": team}
            for t in range(0, duration + 1, 16)
            for pid, name, z, team in [("111", "Alice", -400, 2), ("222", "Bob", -600, 3)]
        ], strict=False)
        states[rid] = states[rid].with_columns((states[rid]["steamid"].replace_strict({"111": 0, "222": 1})).alias('player_color_id'))
    images = {}
    if nuke:
        path = tmp_path / "radar.png"
        Image.new("RGB", (1024, 1024)).save(path)
        images = {"upper": path, "lower": path}
    data = playback_payload(states, rows, 16, RADARS["de_nuke"] if nuke else None, images)
    # Match the component's inline HTML delivery, without browser file access.
    page.set_content(playback_html(data))
    page.wait_for_function("Number(document.querySelector('canvas').dataset.drawCount)>0")
    return playback_html(data)


def test_drag_renders_before_mouse_release_without_network(page, tmp_path):
    open_player(page, tmp_path)
    requests = []
    page.on("request", lambda request: requests.append(request.url))
    box = page.locator("#timeline").bounding_box()
    page.mouse.move(box["x"] + box["width"] * .1, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] * .6, box["y"] + box["height"] / 2, steps=8)
    # Assert while the pointer is still down: updates are not deferred to change/release.
    page.wait_for_function("Number(document.querySelector('canvas').dataset.renderTick)>150")
    page.mouse.up()
    assert requests == []


def test_play_pause_end_loop_overlay_and_player_filters(page, tmp_path):
    open_player(page, tmp_path)
    canvas = page.locator("canvas")
    assert canvas.get_attribute("data-visible-markers") == "4"
    page.locator('[data-solo="111"]').click()
    page.wait_for_function("document.querySelector('canvas').dataset.visibleMarkers==='2'")
    page.locator('input[data-player="222"]').check()
    page.wait_for_function("document.querySelector('canvas').dataset.visibleMarkers==='4'")
    page.locator('input[data-round="2"]').uncheck()
    page.wait_for_function("document.querySelector('canvas').dataset.visibleMarkers==='2'")
    page.locator("#play").click()
    page.wait_for_function("Number(document.querySelector('canvas').dataset.renderTick)>2")
    page.locator("#play").click()
    assert page.locator("#play").get_attribute("aria-pressed") == "false"
    page.locator("#timeline").evaluate("e=>{e.value=200;e.dispatchEvent(new Event('input'))}")
    page.locator('input[data-round="2"]').check()
    page.wait_for_function("document.querySelector('canvas').dataset.renderTick==='200'")
    assert canvas.get_attribute("data-visible-markers") == "2"  # Shorter round has ended.
    page.locator("#timeline").evaluate("e=>{e.value=319;e.dispatchEvent(new Event('input'))}")
    page.locator("#speed").select_option("8")
    page.locator("#play").click()
    page.wait_for_function("document.querySelector('#play').getAttribute('aria-pressed')==='false'")
    assert canvas.get_attribute("data-render-tick") == "320"
    page.locator("#loop").check()
    page.locator("#play").click()
    page.wait_for_function("Number(document.querySelector('canvas').dataset.renderTick)<320")
    assert page.locator("#play").get_attribute("aria-pressed") == "true"


def test_nuke_layers_interpolation_and_gap_policy(page, tmp_path):
    open_player(page, tmp_path, nuke=True)
    assert page.locator("canvas").get_attribute("data-visible-markers") == "2"
    page.locator("#floor").select_option("lower")
    page.locator('[data-solo="111"]').click()
    page.wait_for_function("document.querySelector('canvas').dataset.visibleMarkers==='0'")
    page.locator('[data-solo="222"]').click()
    page.wait_for_function("document.querySelector('canvas').dataset.visibleMarkers==='2'")
    marker = page.evaluate("markerAt([[0,100,100,-600,true,100,2],[16,200,100,-600,true,100,2]],8)")
    assert marker["x"] == 150 and marker["interpolated"]
    assert page.evaluate("markerAt([[0,100,100,-600,true,100,2],[64,200,100,-600,true,100,2]],17)") is None
    marker = page.evaluate("markerAt([[0,100,100,-600,true,100,2],[16,200,100,-400,true,100,2]],8)")
    assert not marker["interpolated"]  # Never interpolate across floors.


def test_player_runs_inside_a_sandboxed_component_iframe(page, tmp_path):
    html = open_player(page, tmp_path)
    page.set_content('<iframe sandbox="allow-scripts allow-same-origin" width="1200" height="1040"></iframe>')
    page.locator("iframe").evaluate("(frame, html)=>frame.srcdoc=html", html)
    embedded = page.frame_locator("iframe")
    playwright.expect(embedded.locator("canvas")).to_have_attribute("data-visible-markers", "4")
    embedded.locator("#play").click()
    playwright.expect(embedded.locator("#play")).to_have_attribute("aria-pressed", "true")
    embedded.locator('[data-solo="111"]').click()
    playwright.expect(embedded.locator("canvas")).to_have_attribute("data-visible-markers", "2")


def test_game_colors_dashes_map_size_and_fullscreen(page, tmp_path, visual_evidence):
    open_player(page, tmp_path)
    expected = ["#f4d03f", "#aa78ed", "#63c66f", "#579bf2", "#f39a45"]
    assert page.evaluate("[0,1,2,3,4].map(slotColor)") == expected
    assert page.evaluate("strokeStyleFor([0,0,0,0,true,100,2,0])") == {"color": expected[0], "dash": [8, 5]}
    assert page.evaluate("strokeStyleFor([0,0,0,0,true,100,3,1])") == {"color": expected[1], "dash": []}
    page.locator("#mapSize").select_option("760")
    playwright.expect(page.locator("canvas")).to_have_css("height", "760px")
    page.locator("#fullscreen").click()
    page.wait_for_function("document.fullscreenElement!==null")
    visual_evidence.snapshot("fullscreen", "全屏模式：地图与操作控件一同展开")
    page.locator("#play").click()
    page.wait_for_function("Number(document.querySelector('canvas').dataset.renderTick)>0")
    page.keyboard.press("Escape")
    page.wait_for_function("document.fullscreenElement===null")


def test_independent_window_keeps_time_and_player_selection(page, tmp_path):
    open_player(page, tmp_path)
    page.locator('[data-solo="111"]').click()
    page.locator("#timeline").evaluate("e=>{e.value=80;e.dispatchEvent(new Event('input'))}")
    with page.context.expect_page() as popup_info:
        page.locator("#openWindow").click()
    popup = popup_info.value
    popup.wait_for_load_state()
    playwright.expect(popup.locator("canvas")).to_have_attribute("data-render-tick", "80")
    playwright.expect(popup.locator("canvas")).to_have_attribute("data-visible-markers", "2")
    assert popup.locator('input[data-player="111"]').is_checked()
    assert not popup.locator('input[data-player="222"]').is_checked()


def test_cached_trails_refresh_for_seek_floor_selection_and_resize(page, tmp_path):
    open_player(page, tmp_path, nuke=True)
    key = page.evaluate("trailCacheKey")
    page.locator("#timeline").evaluate("e=>{e.value=8;e.dispatchEvent(new Event('input'))}")
    page.wait_for_function("document.querySelector('canvas').dataset.renderTick==='8'")
    assert page.evaluate("trailCacheKey") == key  # Reuse observed paths between sample ticks.
    page.locator("#timeline").evaluate("e=>{e.value=32;e.dispatchEvent(new Event('input'))}")
    page.wait_for_function("document.querySelector('canvas').dataset.renderTick==='32'")
    new_key = page.evaluate("trailCacheKey")
    assert new_key != key
    page.locator("#floor").select_option("lower")
    page.wait_for_function("old=>trailCacheKey!==old", arg=new_key)
    new_key = page.evaluate("trailCacheKey")
    page.locator('[data-solo="222"]').click()
    page.wait_for_function("old=>trailCacheKey!==old", arg=new_key)
    new_key = page.evaluate("trailCacheKey")
    page.locator("#mapSize").select_option("760")
    page.wait_for_function("old=>trailCacheKey!==old", arg=new_key)


def open_team_utility_player(page, nuke=False):
    rounds = pl.DataFrame([{"round_id": 1, "start_tick": 100, "freeze_end_tick": 110, "end_tick": 220,
                           "team_a_side": 2, "team_b_side": 3, "team_a_name": "G2", "team_b_name": "NAVI"}], strict=False)
    states = pl.DataFrame([
        {"tick": t, "steamid": pid, "name": name, "x": 10., "y": 10., "z": -600.,
         "health": 100, "is_alive": True, "team_num": team, "player_color_id": color}
        for t in range(100, 221, 16)
        for pid, name, team, color in [("1", "Zed", 2, 0), ("2", "Alice", 2, 4), ("3", "Bob", 3, 1)]
    ], strict=False)
    tracks = pl.DataFrame([{"tick": t, "grenade_entity_id": 7, "grenade_type": "CSmokeGrenadeProjectile",
                            "steamid": "1", "name": "Zed", "x": float(t-100), "y": 10., "z": -600.}
                           for t in range(120, 131)], strict=False)
    events = pl.DataFrame([{"tick": tick, "entityid": 7, "event_type": kind, "user_steamid": "1",
                            "user_name": "Zed", "x": 30., "y": 10., "z": -600.}
                           for tick, kind in [(130, "smokegrenade_detonate"), (180, "smokegrenade_expired")]], strict=False)
    data = playback_payload({1: states}, rounds, 16, RADARS["de_nuke"] if nuke else None,
                            utility_tracks=tracks, events=events)
    page.set_content(playback_html(data))
    page.wait_for_function("Number(document.querySelector('canvas').dataset.drawCount)>0")


def test_named_team_selection_color_order_and_search(page, visual_evidence):
    open_team_utility_player(page)
    g2 = page.locator('.team-group[data-team="a"]')
    assert g2.locator('.player').evaluate_all("els=>els.map(e=>e.querySelector('.slot').textContent)") == ["0", "4"]
    assert g2.locator('.player label span').all_text_contents() == ["Zed", "Alice"]
    page.get_by_role("button", name="G2全队", exact=True).click()
    assert page.locator('input[data-player="1"]').is_checked()
    assert page.locator('input[data-player="2"]').is_checked()
    assert not page.locator('input[data-player="3"]').is_checked()
    visual_evidence.snapshot("team-selection", "全队选择：仅勾选 G2 测试阵容")
    page.get_by_role("button", name="NAVI全队", exact=True).click()
    assert not page.locator('input[data-player="1"]').is_checked()
    assert page.locator('input[data-player="3"]').is_checked()
    page.locator('#playerSearch').fill('G2')
    assert g2.is_visible() and not page.locator('.team-group[data-team="b"]').is_visible()


def test_utility_seeking_expiry_filters_and_overview(page, visual_evidence):
    open_team_utility_player(page)
    canvas = page.locator('canvas')
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')
    page.locator('#timeline').evaluate("e=>{e.value=15;e.dispatchEvent(new Event('input'))}")
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    playwright.expect(canvas).to_have_attribute('data-active-utility-ranges', '0')
    page.locator('#utilityList button').click()
    playwright.expect(canvas).to_have_attribute('data-render-tick', '20')
    playwright.expect(canvas).to_have_attribute('data-active-utility-ranges', '1')
    visual_evidence.snapshot("utility-detonation", "道具事件跳转：定位爆开时刻并显示示意范围")
    page.locator('#timeline').evaluate("e=>{e.value=71;e.dispatchEvent(new Event('input'))}")
    playwright.expect(canvas).to_have_attribute('data-active-utility-ranges', '1')
    page.get_by_role('button', name='NAVI全队', exact=True).click()
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')
    page.locator('#utilityFollow').uncheck()
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    page.locator('input[data-utility-type="smoke"]').uncheck()
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')
    page.locator('input[data-utility-type="smoke"]').check()
    page.locator('#timeline').evaluate("e=>{e.value=0;e.dispatchEvent(new Event('input'))}")
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')
    page.locator('#utilityMode').select_option('overview')
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    page.locator('#showUtility').uncheck()
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')


def test_utility_respects_nuke_floor_and_popup_filters(page):
    open_team_utility_player(page, nuke=True)
    page.locator('#utilityMode').select_option('overview')
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utilities', '0')
    page.locator('#floor').select_option('lower')
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utilities', '1')
    page.locator('input[data-utility-type="flash"]').uncheck()
    page.locator('#utilityRanges').uncheck()
    with page.context.expect_page() as popup_info:
        page.locator('#openWindow').click()
    popup = popup_info.value
    popup.wait_for_load_state()
    playwright.expect(popup.locator('canvas')).to_have_attribute('data-visible-utilities', '1')
    assert popup.locator('#utilityMode').input_value() == 'overview'
    assert not popup.locator('input[data-utility-type="flash"]').is_checked()
    assert not popup.locator('#utilityRanges').is_checked()


def seek_player(page, tick):
    page.locator('#timeline').evaluate('(e,t)=>{e.value=t;e.dispatchEvent(new Event("input"))}', tick)
    playwright.expect(page.locator('canvas')).to_have_attribute('data-render-tick', str(tick))


def trail_alpha_at(page, x, y):
    return page.evaluate('''([x,y])=>{
      const [sx,sy]=screen(x,y), ratio=canvas.width/width();
      return trailCtx.getImageData(Math.round(sx*ratio),Math.round(sy*ratio),1,1).data[3];
    }''', [x, y])


def test_player_trail_window_clears_old_pixels_and_ended_rounds(page, tmp_path):
    open_player(page, tmp_path)
    page.get_by_text('播放选项与高级设置', exact=True).click()
    assert page.locator('#trailRetention').input_value() == '10'
    assert page.locator('#utilityRetention').input_value() == '10'
    page.locator('#tickRate').fill('8')
    page.locator('#tickRate').press('Tab')
    seek_player(page, 200)
    assert trail_alpha_at(page, -1968, -1000) == 0  # Sample at tick 32 is older than 10 seconds.
    assert trail_alpha_at(page, -1856, -1000) > 0  # Tick 144 remains inside the window.
    page.locator('#trailRetention').select_option('all')
    page.wait_for_function('trailCtx.getImageData(...screen(-1968,-1000).map(Math.round),1,1).data[3]>0')
    assert trail_alpha_at(page, -1968, -1000) > 0
    page.locator('#trailRetention').select_option('10')
    page.locator('input[data-round="1"]').uncheck()
    seek_player(page, 320)
    assert page.evaluate('trailCtx.getImageData(0,0,trailCanvas.width,trailCanvas.height).data.some(v=>v!==0)') is False
    seek_player(page, 144)
    assert trail_alpha_at(page, -1920, -1000) > 0  # Rewinding restores the observed trail.


def test_utility_expiry_removes_both_markers_and_paths_independently(page):
    open_team_utility_player(page)
    page.get_by_text('播放选项与高级设置', exact=True).click()
    page.locator('#tickRate').fill('1')
    page.locator('#tickRate').press('Tab')
    canvas = page.locator('canvas')
    seek_player(page, 34)  # Smoke's fixed 15-second circle ends at tick 35.
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    playwright.expect(canvas).to_have_attribute('data-active-utility-ranges', '1')
    seek_player(page, 44)  # Circle end 35 + 10 seconds, not yet expired.
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    playwright.expect(canvas).to_have_attribute('data-visible-utility-trails', '1')
    seek_player(page, 45)
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')
    playwright.expect(canvas).to_have_attribute('data-visible-utility-trails', '0')
    page.locator('#trailRetention').select_option('all')
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '0')
    page.locator('#utilityRetention').select_option('all')
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    playwright.expect(canvas).to_have_attribute('data-visible-utility-trails', '1')
    page.locator('#utilityRetention').select_option('10')
    seek_player(page, 20)
    playwright.expect(canvas).to_have_attribute('data-visible-utilities', '1')
    page.locator('#speed').select_option('8')
    assert page.evaluate('retentionTicks("utilityRetention")') == 10


def test_utility_letters_and_retention_survive_independent_window(page):
    open_team_utility_player(page)
    assert page.evaluate('["smoke","flash","he","fire"].map(k=>UTILITY_TYPES[k].symbol)') == ['S', 'F', 'G', 'M']
    labels = page.locator('#utilityTypes').inner_text()
    for label in ['S - 烟雾', 'F - 闪光', 'G - 手雷', 'M - 火']:
        assert label in labels
    page.get_by_text('播放选项与高级设置', exact=True).click()
    page.locator('#trailRetention').select_option('all')
    page.locator('#utilityRetention').select_option('5')
    page.locator('#tickRate').fill('8')
    page.locator('#tickRate').press('Tab')
    with page.context.expect_page() as popup_info:
        page.locator('#openWindow').click()
    popup = popup_info.value
    popup.wait_for_load_state()
    assert popup.locator('#trailRetention').input_value() == 'all'
    assert popup.locator('#utilityRetention').input_value() == '5'
    assert popup.locator('#tickRate').input_value() == '8'


def test_instant_and_unconfirmed_utility_observations_expire(page):
    open_team_utility_player(page)
    page.get_by_text('播放选项与高级设置', exact=True).click()
    page.locator('#tickRate').fill('1')
    page.locator('#tickRate').press('Tab')
    page.evaluate('Object.assign(DATA.rounds[0].utilities[0],{endTick:null,kind:"flash"})')
    seek_player(page, 32)  # Event tick 20 + 3-second flash circle + 10-second retention.
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utilities', '1')
    seek_player(page, 33)
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utilities', '0')
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utility-trails', '0')
    page.evaluate('Object.assign(DATA.rounds[0].utilities[0],{effectTick:null,confirmed:false,radius:0})')
    seek_player(page, 29)  # Last observed tick 20 + 10 seconds; marker is explicitly unconfirmed.
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utilities', '1')
    seek_player(page, 30)
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utilities', '0')
    playwright.expect(page.locator('canvas')).to_have_attribute('data-visible-utility-trails', '0')


@pytest.mark.parametrize("kind,seconds", [("he", 3), ("flash", 3), ("smoke", 15), ("fire", 5)])
def test_fixed_utility_circle_duration_ignores_expiry_and_speed(page, kind, seconds):
    open_team_utility_player(page)
    page.get_by_text("播放选项与高级设置", exact=True).click()
    page.locator("#tickRate").fill("2")
    page.locator("#tickRate").press("Tab")
    page.locator("#speed").select_option("8")
    page.evaluate("kind=>Object.assign(DATA.rounds[0].utilities[0],{kind,endTick:21})", kind)
    seek_player(page, 20 + seconds * 2 - 1)
    playwright.expect(page.locator("canvas")).to_have_attribute("data-active-utility-ranges", "1")
    seek_player(page, 20 + seconds * 2)
    playwright.expect(page.locator("canvas")).to_have_attribute("data-active-utility-ranges", "0")
    playwright.expect(page.locator("canvas")).to_have_attribute("data-visible-utilities", "1")
    page.locator("#utilityMode").select_option("overview")
    playwright.expect(page.locator("canvas")).to_have_attribute("data-active-utility-ranges", "1")
