"""Freeze-end loadout tiers, independent from a team's spending intent."""

import polars as pl

from cs2ta.frames import column, missing
from cs2ta.round_context import OVERTIME_PROP

ECONOMY_PROPS = ["team_num", "current_equip_value", "balance", "start_balance",
                 "cash_spent_this_round", "inventory", "rounds_played_this_phase",
                 "total_rounds_played", "is_warmup_period", OVERTIME_PROP]
ECONOMY_LABELS = {"all": "全部经济", "pistol": "手枪局", "eco": "eco",
                  "force-buy": "force-buy", "4k-buy": "4k-buy", "full-buy": "full-buy",
                  "double-awp": "double-awp", "unknown": "经济未知"}
PISTOLS = {"glock-18", "usp-s", "p2000", "p250", "desert eagle", "dual berettas",
           "five-seven", "tec-9", "cz75-auto", "r8 revolver"}


def _mean(frame, name):
    if name not in frame.columns:
        return None
    values = frame[name].cast(pl.Float64, strict=False)
    return values.mean() if values.null_count() == 0 and values.is_finite().all() and (values >= 0).all() else None


def classify_loadout(players: pl.DataFrame):
    result = {"buy_type": "unknown", "equip_avg": None, "spent_avg": None,
              "awp_count": None, "double_awp": None}
    ids = column(players, "steamid").cast(pl.String)
    if players.height != 5 or ids.null_count() or ids.is_in(["0", ""]).any() or ids.n_unique() != 5:
        return result
    result["equip_avg"], result["spent_avg"] = _mean(players, "current_equip_value"), _mean(players, "cash_spent_this_round")
    inventories = column(players, "inventory").to_list()
    complete_inventory = len(inventories) == 5 and all(isinstance(v, (list, tuple)) for v in inventories)
    if complete_inventory:
        weapons = [[str(w).casefold().removeprefix("weapon_") for w in value] for value in inventories]
        result["awp_count"] = sum(w.count("awp") for w in weapons)
        result["double_awp"] = result["awp_count"] >= 2

    def all_equal(name, expected):
        return name in players.columns and (players[name] == expected).fill_null(False).all()

    phase_start = all_equal("rounds_played_this_phase", 0) or all_equal("total_rounds_played", 0)
    cash = column(players, "start_balance").cast(pl.Float64, strict=False)
    pistol = (phase_start and all_equal("is_warmup_period", False) and all_equal(OVERTIME_PROP, 0)
              and len(cash) == 5 and cash.is_between(0, 800).fill_null(False).all() and complete_inventory
              and sum(any(w in PISTOLS for w in inventory) for inventory in weapons) >= 3)
    average = result["equip_avg"]
    if pistol and average is not None:
        result["buy_type"] = "pistol"
    elif average is not None:
        result["buy_type"] = "eco" if average < 2000 else "force-buy" if average < 4000 else "4k-buy" if average < 5000 else "full-buy"
    return result


def build_round_economy(rounds: pl.DataFrame, states: pl.DataFrame) -> pl.DataFrame:
    rows = []
    for row in rounds.iter_rows(named=True):
        info = {"round_id": row["round_id"]}
        freeze = row.get("freeze_end_tick")
        snapshot = pl.DataFrame()
        if not missing(freeze) and not states.is_empty():
            snapshot = states.filter(pl.col("tick") == min(int(freeze) + 1, int(row["end_tick"])))
        for team in ("team_a", "team_b"):
            side = row.get(f"{team}_side")
            players = snapshot.filter(pl.col("team_num") == side) if not missing(side) and "team_num" in snapshot.columns else pl.DataFrame()
            info.update({f"{team}_{key}": value for key, value in classify_loadout(players).items()})
        rows.append(info)
    schema = {"round_id": pl.Int64}
    for team in ("team_a", "team_b"):
        schema.update({f"{team}_buy_type": pl.String, f"{team}_equip_avg": pl.Float64,
                       f"{team}_spent_avg": pl.Float64, f"{team}_awp_count": pl.Int64,
                       f"{team}_double_awp": pl.Boolean})
    return pl.DataFrame(rows, schema=schema)
