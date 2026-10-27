from cs2ta.frames import missing
import polars as pl

from cs2ta.round_context import OVERTIME_PROP, build_round_context, color_index, filter_rounds


def test_color_mapping_uses_game_value_and_does_not_guess_missing_ids():
    assert [color_index(value) for value in ["yellow", "purple", "green", "blue", "orange"]] == list(range(5))
    assert [color_index(value) for value in [0, "1", 2., 3, 4]] == list(range(5))
    assert all(color_index(value) is None for value in [None, float("nan"), -1, 5, "unknown", True])


def test_roster_identity_survives_side_switch_and_overtime_uses_explicit_state():
    rounds = pl.DataFrame([
        {"round_id": 1, "start_tick": 0, "end_tick": 99},
        {"round_id": 2, "start_tick": 100, "end_tick": 199},
        {"round_id": 3, "start_tick": 200, "end_tick": 299},
    ], strict=False)
    states = pl.DataFrame([
        {"tick": tick, "steamid": pid, "team_num": side, "team_clan_name": name,
         "is_warmup_period": False, "total_rounds_played": played, OVERTIME_PROP: ot}
        for tick, played, ot, switched in [(10, 0, 0, False), (110, 12, 0, True), (210, 24, 1, True)]
        for pid, side, name in [("A-player", 3 if switched else 2, "Alpha"),
                                ("B-player", 2 if switched else 3, "Beta")]
    ], strict=False)
    result = build_round_context(rounds, states)
    assert result["team_a_side"].to_list() == [2, 3, 3]
    assert result["team_b_side"].to_list() == [3, 2, 2]
    assert result["team_a_name"].to_list() == ["Alpha"] * 3
    assert result["is_overtime"].to_list() == [False, False, True]
    assert filter_rounds(result, "team_a", "3", "overtime")["round_id"].to_list() == [3]
    assert filter_rounds(result, "team_b", "2", "regulation")["round_id"].to_list() == [2]


def test_missing_metadata_remains_unknown_even_when_recording_round_number_is_large():
    rounds = pl.DataFrame([{"round_id": 45, "start_tick": 100, "end_tick": 200}], strict=False)
    result = build_round_context(rounds, pl.DataFrame(strict=False))
    assert missing(result["is_overtime"][0])
    assert missing(result["team_a_side"][0])
    assert filter_rounds(result, phase="regulation").is_empty()
    assert filter_rounds(result, phase="overtime").is_empty()
    assert len(filter_rounds(result)) == 1
