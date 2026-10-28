"""Bound replay working sets without changing cached match facts."""

from pathlib import Path
import json

import polars as pl
from polars.testing import assert_frame_equal
import pytest
from streamlit.testing.v1 import AppTest

from cs2ta import comparison, pipeline, playback_ui, replay_data
from cs2ta.replay_data import check_position_budget, read_round_window
from test_comparison import matches


def test_disjoint_round_windows_do_not_load_unselected_ticks(tmp_path):
    path = tmp_path / "observations.parquet"
    frame = pl.DataFrame({"tick": range(10_000), "x": [1.] * 10_000})
    frame.write_parquet(path)
    rounds = pl.DataFrame({"start_tick": [10, 9980], "end_tick": [15, 9985]})
    selected = read_round_window(path, rounds)
    expected = frame.filter(pl.col("tick").is_between(10, 15) | pl.col("tick").is_between(9980, 9985))
    assert_frame_equal(selected, expected)
    assert selected.height == 12
    assert selected.estimated_size() < frame.estimated_size() / 100


def test_empty_utility_cache_stays_valid(tmp_path):
    path = tmp_path / "empty.parquet"
    pl.DataFrame().write_parquet(path)
    assert read_round_window(path, pl.DataFrame()).is_empty()


def test_global_budget_rejects_before_any_state_frame_is_materialized(matches, monkeypatch):
    def unexpected_read(*args, **kwargs):
        raise AssertionError("Over-budget positions must not be loaded")
    monkeypatch.setattr(comparison, "read_parquet", unexpected_read)
    with pytest.raises(ValueError, match="当前上限"):
        comparison.comparison_payload(matches, [(item["id"], 1) for item in matches], 16, max_rows=99)


def test_round_picker_does_not_load_event_tables(matches, monkeypatch):
    original = replay_data.read_parquet
    reads = []
    def record(path):
        reads.append(Path(path).name)
        assert Path(path).name != "events.parquet"
        return original(path)
    monkeypatch.setattr(replay_data, "read_parquet", record)
    assert comparison.comparison_rounds(matches).height == 4
    assert "rounds.parquet" in reads


def test_light_replay_uses_coarse_positions_and_skips_utility_parsing(matches, monkeypatch):
    calls = []
    original = playback_ui.parse_rounds_positions
    def record(source, destination, ids, step):
        calls.append(step)
        return original(source, destination, ids, step)
    def unexpected_utility(*args):
        raise AssertionError("Default light playback must not parse utility")
    monkeypatch.setattr(playback_ui, "parse_rounds_positions", record)
    monkeypatch.setattr(playback_ui, "parse_utility_tracks", unexpected_utility)
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30)
    app.session_state["uploaded_matches"] = {item["id"]: item for item in matches}
    app.session_state["replay_view"] = "世界坐标"
    app.run()
    assert app.checkbox(key="replay_low_memory").value is True
    assert app.checkbox(key="replay_utility").value is False
    app.button(key="select_filtered_rounds").click().run()
    app.button(key="load_replay").click().run()
    assert not app.exception and not app.error
    assert calls == [64, 64]
    assert len(app.get("iframe")) == 1
    # Cached light positions are not reparsed on a second request.
    calls.clear()
    app.button(key="load_replay").click().run()
    assert not calls and not app.error


def test_light_round_cap_can_be_explicitly_disabled():
    app = AppTest.from_string("""
from pathlib import Path
import polars as pl
from cs2ta.playback_ui import render_round_player
rows = pl.DataFrame({"round_id": [1,2,3,4,5], "start_tick": [0]*5, "end_tick": [100]*5})
render_round_player(Path("memory-cap-fixture"), "unused.dem", rows, 1, "unknown_map")
""").run()
    app.button(key="select_filtered_rounds").click().run()
    assert not app.exception
    assert any("最多加载 4 个回合" in message.value for message in app.warning)
    assert not any(button.key == "load_replay" for button in app.button)
    app.checkbox(key="replay_low_memory").uncheck().run()
    assert not app.exception
    assert app.button(key="load_replay")
