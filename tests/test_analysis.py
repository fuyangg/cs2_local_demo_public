from cs2ta.frames import missing
import polars as pl
import pytest

from cs2ta.analysis import build_rounds, events_for_round, kill_summary, normalize_states


def test_restart_does_not_join_previous_start_to_later_end():
    events = pl.DataFrame([
        {"tick": 1, "event_type": "round_start", "is_warmup_period": True},
        {"tick": 10, "event_type": "round_start", "is_warmup_period": False},
        {"tick": 20, "event_type": "round_freeze_end"},
        {"tick": 30, "event_type": "round_end", "winner": 3},
        {"tick": 40, "event_type": "round_start"},
        {"tick": 50, "event_type": "round_end", "winner": 2},
        {"tick": 60, "event_type": "round_start"},
    ], strict=False)
    rounds = build_rounds(events)
    assert rounds["start_tick"].to_list() == [10, 40]
    assert rounds["freeze_end_tick"][0] == 20
    assert missing(rounds["freeze_end_tick"][1])
    assert missing(rounds["warmup"][1])
    assert events_for_round(events, rounds.row(0, named=True))["tick"].to_list() == [10, 20, 30]


def test_kills_use_identity_not_nickname_and_exclude_world_and_suicide():
    events = pl.DataFrame([
        {"event_type": "player_death", "tick": 1, "attacker_steamid": "76561198000000001", "user_steamid": "76561198000000002", "attacker_name": "same", "headshot": True},
        {"event_type": "player_death", "tick": 2, "attacker_steamid": "76561198000000002", "user_steamid": "76561198000000001", "attacker_name": "same", "headshot": False},
        {"event_type": "player_death", "tick": 3, "attacker_steamid": "0", "user_steamid": "76561198000000002"},
        {"event_type": "player_death", "tick": 4, "attacker_steamid": "76561198000000001", "user_steamid": "76561198000000001"},
    ], strict=False)
    summary = kill_summary(events)
    assert len(summary) == 2
    assert summary["kills"].sum() == 2
    assert summary["headshots"].sum() == 1


def test_multiple_deaths_same_tick_are_preserved():
    events = pl.DataFrame([{"event_type": "player_death", "tick": 10, "attacker_steamid": "123"}] * 2, strict=False)
    assert kill_summary(events)["kills"][0] == 2


def test_duplicate_player_state_fails_instead_of_silent_dedup():
    state = {"tick": 1, "steamid": "123", "X": 1, "Y": 2, "Z": 3, "team_num": 2, "is_alive": True}
    with pytest.raises(ValueError, match="重复"):
        normalize_states(pl.DataFrame([state, state], strict=False))


def test_empty_rounds_have_stable_schema():
    result = build_rounds(pl.DataFrame(schema=["tick", "event_type"], strict=False))
    assert result.is_empty()
    assert "freeze_end_tick" in result
