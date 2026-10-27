from polars.testing import assert_frame_equal
import json

import polars as pl
import pytest

from cs2ta.playback import playback_html, playback_payload
from cs2ta.radar import RADARS


def rounds_fixture():
    return pl.DataFrame([
        {"round_id": 1, "start_tick": 100, "freeze_end_tick": 116, "end_tick": 164},
        {"round_id": 2, "start_tick": 500, "freeze_end_tick": None, "end_tick": 532},
    ], strict=False)


def states_fixture(start=100, name="player"):
    return pl.DataFrame([
        {"tick": start + offset, "steamid": "123", "name": name, "x": -2476. + offset,
         "y": 3239., "z": -400., "team_num": 2, "is_alive": True, "health": 100}
        for offset in [0, 16, 32]
    ], strict=False)


def test_overlay_uses_relative_ticks_and_preserves_individual_endpoints():
    data = playback_payload({1: states_fixture(), 2: states_fixture(500)}, rounds_fixture(), 16,
                            alignment="round_start")
    assert [r["anchor"] for r in data["rounds"]] == [100, 500]
    assert [r["duration"] for r in data["rounds"]] == [64, 32]
    assert [r["tracks"][0]["samples"][1][0] for r in data["rounds"]] == [16, 16]
    assert len(data["players"]) == 1  # Same identity across rounds.
    assert data["maxTick"] == 64


def test_freeze_end_alignment_and_explicit_fallback():
    data = playback_payload({1: states_fixture(), 2: states_fixture(500)}, rounds_fixture(), 16,
                            alignment="freeze_end")
    assert data["rounds"][0]["anchor"] == 116
    assert data["rounds"][0]["tracks"][0]["samples"][0][0] == -16
    assert not data["rounds"][0]["fallback"]
    assert data["rounds"][1]["fallback"]
    assert data["rounds"][1]["anchor"] == 500


def test_payload_projection_does_not_mutate_original_states():
    states = states_fixture()
    before = states.clone()
    data = playback_payload({1: states}, rounds_fixture(), 16, RADARS["de_dust2"])
    assert data["rounds"][0]["tracks"][0]["samples"][0][1:3] == [0., 0.]
    assert_frame_equal(before, states)


def test_missing_values_and_demo_text_are_safe_in_html():
    states = states_fixture(name='</script><script>alert("demo")</script>')
    states = states.with_columns(pl.Series("x", [-2476., float("nan"), -2444.]))
    data = playback_payload({1: states}, rounds_fixture(), 16)
    html = playback_html(data)
    assert '</script><script>alert("demo")' not in html
    assert "\\u003c/script\\u003e" in html
    assert data["rounds"][0]["tracks"][0]["samples"][1][1] is None
    json.dumps(data, allow_nan=False)


def test_reject_duplicate_player_samples():
    states = states_fixture()
    with pytest.raises(ValueError, match="重复"):
        playback_payload({1: pl.concat([states, states], how="diagonal_relaxed")}, rounds_fixture(), 16)


def test_twenty_rounds_are_supported_and_twenty_one_are_rejected():
    rounds = pl.DataFrame([{"round_id": rid, "start_tick": 100, "end_tick": 132} for rid in range(1, 22)], strict=False)
    states = {rid: states_fixture() for rid in range(1, 22)}
    data = playback_payload(dict(list(states.items())[:20]), rounds, 16)
    assert len(data["rounds"]) == 20
    with pytest.raises(ValueError, match="1–20"):
        playback_payload(states, rounds, 16)


def test_color_id_and_context_reach_the_browser_without_overriding_per_sample_color():
    states = states_fixture()
    states = states.with_columns(pl.Series('player_color', ["yellow", "purple", None], strict=False))
    rounds = rounds_fixture()
    rounds = rounds.with_columns(pl.lit(2).alias('team_a_side'))
    rounds = rounds.with_columns(pl.lit(3).alias('team_b_side'))
    rounds = rounds.with_columns(pl.lit(True).alias('is_overtime'))
    data = playback_payload({1: states}, rounds, 16)
    assert [p[7] for p in data["rounds"][0]["tracks"][0]["samples"]] == [0, 1, None]
    assert data["players"][0]["color"] == 1
    assert data["rounds"][0]["teamASide"] == 2
    assert data["rounds"][0]["isOvertime"] is True


def test_default_alignment_is_freeze_end():
    data = playback_payload({1: states_fixture()}, rounds_fixture(), 16)
    assert data["alignment"] == "freeze_end"
    assert data["rounds"][0]["anchor"] == 116


def test_team_rosters_survive_halftime_and_sort_by_color_not_name():
    rounds = rounds_fixture()
    rounds = rounds.with_columns(pl.Series("team_a_side", [2, 3]), pl.Series("team_b_side", [3, 2]))
    rounds = rounds.with_columns(pl.lit("G2").alias("team_a_name"), pl.lit("NAVI").alias("team_b_name"))
    frames = {}
    for rid, tick in [(1, 100), (2, 500)]:
        frame = pl.concat([states_fixture(tick, "Zed"), states_fixture(tick, "Alice").with_columns(pl.lit("456").alias("steamid")),
                           states_fixture(tick, "Bob").with_columns(pl.lit("789").alias("steamid"))], how="diagonal_relaxed")
        frame = frame.with_columns((frame["steamid"].replace_strict({"123": 0, "456": 4, "789": 1})).alias('player_color_id'))
        frame = frame.with_columns((frame["steamid"].replace_strict({"123": 2 if rid == 1 else 3,
                                               "456": 2 if rid == 1 else 3, "789": 3 if rid == 1 else 2})).alias('team_num'))
        frames[rid] = frame
    data = playback_payload(frames, rounds, 16)
    assert [(p["name"], p["teamName"], p["color"]) for p in data["players"]] == [
        ("Zed", "G2", 0), ("Alice", "G2", 4), ("Bob", "NAVI", 1)]
