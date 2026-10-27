import polars as pl
import pytest

from cs2ta.economy import build_round_economy, classify_loadout
from cs2ta.round_context import OVERTIME_PROP, filter_rounds


def team(value=4000, side=2, prefix="A", tick=11):
    return pl.DataFrame([{"steamid": f"{prefix}{i}", "team_num": side, "tick": tick,
                          "current_equip_value": value, "cash_spent_this_round": 1000,
                          "start_balance": 5000, "inventory": ["AK-47", "knife"],
                          "rounds_played_this_phase": 2, "total_rounds_played": 2,
                          "is_warmup_period": False, OVERTIME_PROP: 0} for i in range(5)], strict=False)


@pytest.mark.parametrize("value,label", [(0,"eco"),(1999,"eco"),(2000,"force-buy"),(3999,"force-buy"),
                                         (4000,"4k-buy"),(4999,"4k-buy"),(5000,"full-buy")])
def test_equipment_tier_boundaries_include_saved_gear_not_spending(value, label):
    result = classify_loadout(team(value))
    assert result["buy_type"] == label
    assert result["equip_avg"] == value and result["spent_avg"] == 1000


def test_double_awp_is_independent_of_buy_tier_and_missing_inventory_is_unknown():
    players = team(5500)
    players = players.with_columns(pl.Series("inventory", [["AWP", "knife"], ["weapon_awp"], *[["AK-47", "knife"] for _ in range(3)]]))
    result = classify_loadout(players)
    assert result["buy_type"] == "full-buy" and result["awp_count"] == 2 and result["double_awp"]
    players = players.with_columns(pl.Series("inventory", [["AWP", "knife"], ["weapon_awp"], None, ["AK-47", "knife"], ["AK-47", "knife"]]))
    result = classify_loadout(players)
    assert result["buy_type"] == "full-buy" and result["double_awp"] is None


def test_pistol_both_halves_with_dropped_pistol_but_not_overtime_or_knife():
    players = team(800)
    players = players.with_columns(pl.lit(800).alias("start_balance"), pl.lit(0).alias("rounds_played_this_phase"))
    players = players.with_columns(pl.lit(12).alias('total_rounds_played'))  # Recognition uses phase metadata, not a hardcoded round number.
    players = players.with_columns(pl.Series('inventory', [["USP-S"] for _ in range(5)], strict=False))
    players = players.with_columns(pl.Series("inventory", [["knife"], *[["USP-S"] for _ in range(4)]]))
    assert classify_loadout(players)["buy_type"] == "pistol"
    players = players.with_columns(pl.lit(1).alias(OVERTIME_PROP))
    assert classify_loadout(players)["buy_type"] == "eco"
    players = players.with_columns(pl.lit(0).alias(OVERTIME_PROP))
    players = players.with_columns(pl.Series('inventory', [["knife"] for _ in range(5)], strict=False))
    assert classify_loadout(players)["buy_type"] == "eco"


def test_incomplete_roster_and_missing_equipment_do_not_become_eco():
    assert classify_loadout(team().head(4))["buy_type"] == "unknown"
    players = team()
    players = players.with_columns(pl.Series("current_equip_value", [None, 4000, 4000, 4000, 4000]))
    assert classify_loadout(players)["buy_type"] == "unknown"
    players = team()
    players = players.with_columns(pl.Series("steamid", ["0", "A1", "A2", "A3", "A4"]))
    assert classify_loadout(players)["buy_type"] == "unknown"


def test_economy_uses_exact_freeze_snapshot_and_stable_team_mapping():
    rounds = pl.DataFrame([
        {"round_id":1,"start_tick":0,"freeze_end_tick":10,"end_tick":50,"team_a_side":2,"team_b_side":3},
        {"round_id":2,"start_tick":100,"freeze_end_tick":110,"end_tick":150,"team_a_side":3,"team_b_side":2},
        {"round_id":3,"start_tick":200,"freeze_end_tick":None,"end_tick":250,"team_a_side":3,"team_b_side":2}], strict=False)
    states = pl.concat([team(5000),team(500,side=3,prefix="B"),team(4200,side=3,tick=111),
                        team(500,side=2,prefix="B",tick=111),team(9000,tick=12)], how="diagonal_relaxed")
    result = rounds.join(build_round_economy(rounds,states),on="round_id", maintain_order="left")
    assert result["team_a_buy_type"].to_list() == ["full-buy","4k-buy","unknown"]
    assert result["team_b_buy_type"].to_list() == ["eco","eco","unknown"]
    assert filter_rounds(result,economy="4k-buy")["round_id"].to_list() == [2]
    assert filter_rounds(result,team="team_b",economy="eco")["round_id"].to_list() == [1,2]
    assert filter_rounds(result,economy="unknown")["round_id"].to_list() == [3]
    assert filter_rounds(result,economy="double-awp").is_empty()


def test_unknown_filter_includes_missing_old_cache_rows():
    rounds = pl.DataFrame({"round_id":[1,2,3], "team_a_buy_type":["full-buy",None,"unknown"]}, strict=False)
    assert filter_rounds(rounds, economy="unknown")["round_id"].to_list() == [2,3]
    assert filter_rounds(rounds.drop("team_a_buy_type"), economy="unknown")["round_id"].to_list() == [1,2,3]
    assert filter_rounds(rounds, economy="double-awp").is_empty()
