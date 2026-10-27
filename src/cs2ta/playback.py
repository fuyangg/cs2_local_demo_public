"""Compact, offline browser playback data; original facts stay in Parquet."""

import base64
import json
import math
from pathlib import Path

import polars as pl

from cs2ta.frames import column, missing, nulls
from cs2ta.radar import Radar
from cs2ta.round_context import color_index
from cs2ta.utility import utility_payload

MAX_PLAYBACK_ROWS = 1000000


def _number(value):
    if missing(value):
        return None
    number = float(value)
    return round(number, 3) if math.isfinite(number) else None


def playback_payload(
    states_by_round: dict[int, pl.DataFrame], rounds: pl.DataFrame, tick_step: int,
    radar: Radar | None = None, images: dict[str, Path] | None = None,
    alignment: str = "freeze_end",
    *, utility_tracks: pl.DataFrame | None = None, events: pl.DataFrame | None = None,
) -> dict:
    if alignment not in {"round_start", "freeze_end"} or tick_step < 1:
        raise ValueError("回放对齐方式或采样间隔无效。")
    if not states_by_round or len(states_by_round) > 20:
        raise ValueError("回放支持同时加载 1–20 个回合。")
    if sum(len(states) for states in states_by_round.values()) > MAX_PLAYBACK_ROWS:
        raise ValueError("所选位置超过 100 万条，请减少回合数或增大采样间隔。")
    output_rounds, players = [], {}
    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    for round_id, states in states_by_round.items():
        row = rounds.filter(pl.col("round_id") == round_id)
        if row.is_empty():
            raise ValueError(f"没有回合 {round_id}。")
        row = row.row(0, named=True)
        freeze = row.get("freeze_end_tick")
        fallback = alignment == "freeze_end" and missing(freeze)
        anchor = int(freeze) if alignment == "freeze_end" and not fallback else int(row["start_tick"])
        duration = int(row["end_tick"]) - anchor
        if duration < 0:
            raise ValueError(f"回合 {round_id} 的时间锚点晚于结束。")
        source = nulls(states).filter(pl.col("tick").is_between(row["start_tick"], row["end_tick"]))
        if source.select("steamid", "tick").is_duplicated().any():
            raise ValueError(f"回合 {round_id} 存在重复玩家状态。")
        tracks = []
        for group in source.sort("steamid").partition_by("steamid", maintain_order=True):
            pid = str(group["steamid"][0])
            group = group.sort("tick")
            name = group.row(-1, named=True).get("name", pid)
            colors = [color_index(value) for value in column(group, "player_color_id" if "player_color_id" in group.columns else "player_color")]
            colors = [value for value in colors if value is not None]
            chosen_color = int(colors[-1]) if colors else None
            # Stable A/B round context survives halftime; never group by current T/CT.
            sides = group["team_num"].drop_nulls()
            side = sides[0] if len(sides) else None
            team, team_name = "unknown", "战队未知"
            for key in ("a", "b"):
                mapped = row.get(f"team_{key}_side")
                if side is not None and not missing(mapped) and side == mapped:
                    team = key
                    label = row.get(f"team_{key}_name")
                    team_name = str(label) if not missing(label) and str(label).strip() else f"队伍 {key.upper()}"
                    break
            if team == "unknown" and "team_clan_name" in group.columns:
                labels = group["team_clan_name"].drop_nulls().cast(pl.String)
                labels = labels.filter(labels.str.strip_chars() != "")
                if len(labels):
                    team_name = labels[0]
                    team = "name:" + team_name
            previous = players.get(pid)
            if previous and team == "unknown":
                team, team_name = previous["team"], previous["teamName"]
            players[pid] = {"id": pid, "name": str(name) if not missing(name) else pid,
                            "color": chosen_color, "team": team, "teamName": team_name}
            samples = []
            # Keep one preceding sample for a freeze-end anchor between sample ticks.
            before = group.filter(pl.col("tick") < anchor).tail(1)
            group = pl.concat([before, group.filter(pl.col("tick") >= anchor)])
            for sample in group.iter_rows(named=True):
                x, y = _number(sample["x"]), _number(sample["y"])
                if x is not None and y is not None:
                    x, y = radar.project(x, y) if radar else (x, -y)
                    x, y = round(float(x), 3), round(float(y), 3)
                    bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
                alive = None if missing(sample["is_alive"]) else bool(sample["is_alive"])
                samples.append([int(sample["tick"]) - anchor, x, y, _number(sample["z"]), alive,
                                _number(sample.get("health")), _number(sample["team_num"]),
                                color_index(sample.get("player_color_id", sample.get("player_color")))])
            tracks.append({"player": pid, "samples": samples})
        utilities = utility_payload(utility_tracks, events, int(row["start_tick"]), int(row["end_tick"]), anchor, radar)
        for item in utilities:
            for sample in item["samples"]:
                x, y = sample[1:3]
                bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
            if item["point"]:
                x, y = item["point"][:2]
                bounds = [min(bounds[0], x), min(bounds[1], y), max(bounds[2], x), max(bounds[3], y)]
        output_rounds.append({"utilities": utilities, "id": int(round_id), "anchor": anchor, "duration": duration,
                              "fallback": fallback, "tracks": tracks,
                              "teamASide": _number(row.get("team_a_side")),
                              "teamBSide": _number(row.get("team_b_side")),
                              "teamAName": str(row.get("team_a_name", "队伍 A")),
                              "teamBName": str(row.get("team_b_name", "队伍 B")),
                              "isOvertime": bool(row["is_overtime"]) if not missing(row.get("is_overtime")) else None,
                              "officialRound": _number(row.get("official_round_number"))})
    if radar:
        bounds = [0, 0, 1024, 1024]
    elif not all(math.isfinite(value) for value in bounds):
        bounds = [-100, -100, 100, 100]
    else:
        bounds = [bounds[0] - 100, bounds[1] - 100, bounds[2] + 100, bounds[3] + 100]
    encoded = {}
    for layer, path in (images or {}).items():
        encoded[layer] = "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "rounds": output_rounds, "players": sorted(players.values(), key=lambda p: (p["team"], p["color"] if p["color"] is not None else 5, p["id"])),
        "tickStep": tick_step, "maxTick": max(item["duration"] for item in output_rounds),
        "bounds": bounds, "images": encoded, "alignment": alignment,
        "map": {"name": radar.name, "posX": radar.pos_x, "posY": radar.pos_y,
                "scale": radar.scale, "lowerMaxZ": radar.lower_max_z} if radar else None,
    }


def playback_html(payload: dict) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    # Demo names are untrusted text; never allow a closing script tag in JSON.
    serialized = serialized.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = (Path(__file__).parent / "frontend/player.html").read_text(encoding="utf-8")
    return template.replace("__PAYLOAD_JSON__", serialized)
