"""Round identities and explicit overtime metadata from sparse demo snapshots."""

import polars as pl

from cs2ta.frames import column, missing, nulls

OVERTIME_PROP = "CCSGameRulesProxy.CCSGameRules.m_nOvertimePlaying"
CONTEXT_PROPS = ["player_color", "team_num", "team_clan_name", "total_rounds_played",
                 "is_warmup_period", OVERTIME_PROP]
COLOR_NAMES = ["yellow", "purple", "green", "blue", "orange"]
PLAYER_COLORS = ["#f4d03f", "#aa78ed", "#63c66f", "#579bf2", "#f39a45"]


def color_index(value) -> int | None:
    if missing(value) or isinstance(value, bool):
        return None
    if isinstance(value, str) and value.lower() in COLOR_NAMES:
        return COLOR_NAMES.index(value.lower())
    try:
        number = float(value)
        return int(number) if number.is_integer() and 0 <= number <= 4 else None
    except (TypeError, ValueError):
        return None


def _unique_value(frame, name):
    values = column(frame, name).drop_nulls().unique()
    return values[0] if len(values) == 1 else None


def build_round_context(rounds: pl.DataFrame, states: pl.DataFrame) -> pl.DataFrame:
    """A/B follow unambiguous Steam ID roster overlap, surviving side switches."""
    states = nulls(states)
    rows, rosters, team_names = [], None, {"a": "队伍 A", "b": "队伍 B"}
    for row in rounds.sort("start_tick").iter_rows(named=True):
        subset = states.filter(pl.col("tick").is_between(row["start_tick"], row["end_tick"])) if "tick" in states.columns else pl.DataFrame()
        snapshot = subset
        if not subset.is_empty():
            for candidate in subset.sort("tick").partition_by("tick", maintain_order=True):
                if "team_num" in candidate.columns and {2, 3}.issubset(set(candidate["team_num"].drop_nulls())):
                    snapshot = candidate
                    break
        warmup, overtime, played = (_unique_value(snapshot, name) for name in ("is_warmup_period", OVERTIME_PROP, "total_rounds_played"))
        info = {"round_id": row["round_id"], "team_a_side": None, "team_b_side": None,
                "team_a_name": team_names["a"], "team_b_name": team_names["b"],
                "is_overtime": bool(overtime > 0) if overtime is not None else None,
                "official_round_number": int(played) + 1 if played is not None else None}
        side_rosters = {}
        if {"team_num", "steamid"}.issubset(snapshot.columns):
            for side in (2, 3):
                side_rosters[side] = set(snapshot.filter(pl.col("team_num") == side)["steamid"].drop_nulls().cast(pl.String)) - {"0", ""}
        if warmup is not None and not bool(warmup) and all(side_rosters.get(side) for side in (2, 3)):
            if rosters is None:
                rosters = {"a": set(side_rosters[2]), "b": set(side_rosters[3])}
            assignments = {}
            for side in (2, 3):
                scores = {team: len(roster & side_rosters[side]) for team, roster in rosters.items()}
                if max(scores.values()) > 0 and scores["a"] != scores["b"]:
                    assignments[side] = max(scores, key=scores.get)
            if len(assignments) == 2 and len(set(assignments.values())) == 2:
                for side, team in assignments.items():
                    info[f"team_{team}_side"] = side
                    rosters[team].update(side_rosters[side])
                    labels = column(snapshot.filter(pl.col("team_num") == side), "team_clan_name").drop_nulls().cast(pl.String)
                    labels = labels.filter(labels.str.strip_chars() != "")
                    if labels.n_unique() == 1:
                        team_names[team] = labels[0]
                info["team_a_name"], info["team_b_name"] = team_names["a"], team_names["b"]
        rows.append(info)
    schema = {"round_id": pl.Int64, "team_a_side": pl.Int64, "team_b_side": pl.Int64,
              "team_a_name": pl.String, "team_b_name": pl.String, "is_overtime": pl.Boolean,
              "official_round_number": pl.Int64}
    return pl.DataFrame(rows, schema=schema)


def filter_rounds(rounds: pl.DataFrame, team="team_a", side="all", phase="all", economy="all") -> pl.DataFrame:
    mask = pl.lit(True)
    if side != "all":
        name = f"{team}_side"
        mask &= (pl.col(name) == int(side)).fill_null(False) if name in rounds.columns else pl.lit(False)
    if phase != "all":
        mask &= (pl.col("is_overtime") == (phase == "overtime")).fill_null(False) if "is_overtime" in rounds.columns else pl.lit(False)
    if economy != "all":
        name = f"{team}_double_awp" if economy == "double-awp" else f"{team}_buy_type"
        if name in rounds.columns and economy == "unknown":
            mask &= pl.col(name).fill_null("unknown") == "unknown"
        elif name in rounds.columns:
            mask &= (pl.col(name) == (True if economy == "double-awp" else economy)).fill_null(False)
        else:
            mask &= pl.lit(economy == "unknown")
    return nulls(rounds).filter(mask)
