from streamlit.testing.v1 import AppTest


APP = '''
import polars as pl
from cs2ta.round_picker import pick_rounds
rounds = pl.DataFrame([{
    "round_id": rid, "team_a_side": 2 if rid <= 12 else 3,
    "team_b_side": 3 if rid <= 12 else 2, "is_overtime": rid >= 25,
    "team_a_name": "Alpha", "team_b_name": "Beta"
} for rid in range(1, 31)])
pick_rounds(rounds, "fixture", 1)
'''


def test_quick_team_side_overtime_selection_and_twenty_round_cap():
    app = AppTest.from_string(APP).run()
    assert not app.exception
    assert app.button(key="select_filtered_rounds").disabled  # 30 matches, not silently truncated.
    app.button(key="select_first_twenty").click().run()
    assert len(app.session_state["replay_selected:fixture"]) == 20
    app.selectbox(key="round_filter_side").select("3").run()
    app.selectbox(key="round_filter_phase").select("overtime").run()
    app.button(key="select_filtered_rounds").click().run()
    assert app.session_state["replay_selected:fixture"] == list(range(25, 31))
    app.selectbox(key="round_filter_team").select("team_b").run()
    app.selectbox(key="round_filter_side").select("2").run()
    app.button(key="select_filtered_rounds").click().run()
    assert app.session_state["replay_selected:fixture"] == list(range(25, 31))
    app.button(key="clear_replay_rounds").click().run()
    assert app.session_state["replay_selected:fixture"] == []
    assert not app.exception


def test_replay_ui_defaults_to_freeze_end(tmp_path):
    app = AppTest.from_string('''
from pathlib import Path
import polars as pl
from cs2ta.playback_ui import render_round_player
rounds = pl.DataFrame([{"round_id": 1, "start_tick": 100, "freeze_end_tick": 116, "end_tick": 164}])
render_round_player(Path("''' + str(tmp_path) + '''"), "fixture.dem", rounds, 1, "unknown_map")
''').run()
    assert not app.exception
    assert app.selectbox(key="replay_alignment").value == "freeze_end"
    assert app.checkbox(key="replay_utility").value is False
    assert app.checkbox(key="replay_low_memory").value is True


def test_economy_team_side_phase_filters_combine_and_keep_manual_selection():
    app = AppTest.from_string(APP.replace('pick_rounds(rounds, "fixture", 1)', '''
rounds = rounds.with_columns(
    pl.Series("team_a_buy_type", ["full-buy" if rid % 2 else "eco" for rid in rounds["round_id"]]),
    pl.lit("4k-buy").alias("team_b_buy_type"),
    (pl.col("round_id") == 27).alias("team_a_double_awp"),
    pl.lit(False).alias("team_b_double_awp"))
pick_rounds(rounds, "fixture", 1)
''')).run()
    app.selectbox(key="round_filter_phase").select("overtime").run()
    app.selectbox(key="round_filter_economy").select("full-buy").run()
    app.button(key="select_filtered_rounds").click().run()
    assert app.session_state["replay_selected:fixture"] == [25,27,29]
    app.selectbox(key="round_filter_economy").select("double-awp").run()
    app.button(key="select_filtered_rounds").click().run()
    assert app.session_state["replay_selected:fixture"] == [27]
    app.selectbox(key="round_filter_team").select("team_b").run()
    assert app.button(key="select_filtered_rounds").disabled
    assert app.session_state["replay_selected:fixture"] == [27]
    app.selectbox(key="round_filter_economy").select("4k-buy").run()
    app.button(key="select_filtered_rounds").click().run()
    assert app.session_state["replay_selected:fixture"] == list(range(25,31))
    assert not app.exception
