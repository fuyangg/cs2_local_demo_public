"""Pure Polars transformations, independent of parser and UI."""

import polars as pl

from cs2ta.frames import missing, nulls

ROUND_SCHEMA = {"round_id": pl.Int64, "start_tick": pl.Int64, "freeze_end_tick": pl.Int64,
                "end_tick": pl.Int64, "winner": pl.Int64, "warmup": pl.Boolean}


def build_rounds(events: pl.DataFrame) -> pl.DataFrame:
    """Keep complete rounds; recording ordinals are not official score numbers."""
    events = nulls(events)
    starts = events.filter(pl.col("event_type") == "round_start").sort("tick").to_dicts()
    ends = events.filter(pl.col("event_type") == "round_end").sort("tick")
    freezes = events.filter(pl.col("event_type") == "round_freeze_end").sort("tick")
    rows = []
    for i, start in enumerate(starts):
        next_start = starts[i + 1]["tick"] if i + 1 < len(starts) else None
        predicate = pl.col("tick") >= start["tick"]
        if next_start is not None:
            predicate &= pl.col("tick") < next_start
        candidates = ends.filter(predicate)
        if candidates.is_empty():
            continue
        end = candidates.row(0, named=True)
        freeze = freezes.filter(pl.col("tick").is_between(start["tick"], end["tick"]))
        warmup = start.get("is_warmup_period")
        # Parser versions/demos can report either side names or numeric codes.
        winner = end.get("winner")
        if isinstance(winner, str):
            winner = winner.strip().upper()
        winner = {2: 2, 3: 3, "2": 2, "3": 3, "T": 2, "CT": 3}.get(winner)
        rows.append({"round_id": len(rows) + 1, "start_tick": int(start["tick"]),
                     "freeze_end_tick": int(freeze["tick"][0]) if not freeze.is_empty() else None,
                     "end_tick": int(end["tick"]), "winner": winner,
                     "warmup": bool(warmup) if not missing(warmup) else None})
    return pl.DataFrame(rows, schema=ROUND_SCHEMA, strict=False)


def events_for_round(events: pl.DataFrame, round_row: dict) -> pl.DataFrame:
    return events.filter(pl.col("tick").is_between(round_row["start_tick"], round_row["end_tick"]))


def kill_summary(events: pl.DataFrame) -> pl.DataFrame:
    """Count by Steam ID, excluding world/self kills; team kills remain included."""
    schema = {"steamid": pl.String, "name": pl.String, "kills": pl.UInt32, "headshots": pl.UInt32}
    deaths = nulls(events).filter(pl.col("event_type") == "player_death")
    if deaths.is_empty() or "attacker_steamid" not in deaths.columns:
        return pl.DataFrame(schema=schema)
    deaths = deaths.with_columns(pl.col("attacker_steamid").cast(pl.String).alias("steamid"))
    valid = pl.col("steamid").is_not_null() & ~pl.col("steamid").is_in(["", "0"])
    if "user_steamid" in deaths.columns:
        valid &= (pl.col("steamid") != pl.col("user_steamid").cast(pl.String)).fill_null(True)
    deaths = deaths.filter(valid).with_columns(
        (pl.col("attacker_name").cast(pl.String) if "attacker_name" in deaths.columns else pl.lit(None, pl.String)).alias("name"),
        (pl.col("headshot").cast(pl.Boolean).fill_null(False) if "headshot" in deaths.columns else pl.lit(False)).alias("headshots"))
    return deaths.group_by("steamid").agg(
        pl.col("name").last(), pl.len().alias("kills"), pl.col("headshots").sum()
    ).select(list(schema)).sort(["kills", "steamid"], descending=[True, False])


def normalize_states(states: pl.DataFrame) -> pl.DataFrame:
    required = {"tick", "steamid", "X", "Y", "Z", "team_num", "is_alive"}
    absent = required - set(states.columns)
    if absent:
        raise ValueError(f"位置数据缺少字段：{sorted(absent)}")
    result = nulls(states).rename({"X": "x", "Y": "y", "Z": "z"}).with_columns(pl.col("steamid").cast(pl.String))
    if result.select("tick", "steamid").is_duplicated().any():
        raise ValueError("位置数据存在重复的 tick + steamid，未自动丢弃。")
    return result.sort(["steamid", "tick"])
