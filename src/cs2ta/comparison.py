"""Combine same-map match facts without conflating rounds, players or teams."""

import json
from pathlib import Path

import polars as pl

from cs2ta.frames import read_parquet
from cs2ta.pipeline import load_dataset, position_cache_path
from cs2ta.playback import MAX_PLAYBACK_ROWS, playback_payload
from cs2ta.round_context import filter_rounds


def match_label(match):
    return f"{match['name']} · {match['id'][:8]}"


def same_map(matches):
    maps = {match["map_name"] for match in matches}
    if not matches or len({match["id"] for match in matches}) != len(matches):
        raise ValueError("请选择不同的比赛。")
    if len(maps) != 1 or not next(iter(maps)):
        raise ValueError("只能叠加已确认属于同一地图的比赛。")
    return next(iter(maps))


def comparison_rounds(matches):
    same_map(matches)
    frames, offset = [], 0
    for match in matches:
        _, rounds = load_dataset(Path(match["destination"]))
        frames.append(rounds.with_columns(
            pl.col("round_id").alias("source_round_id"),
            pl.Series("round_id", range(offset + 1, offset + len(rounds) + 1), dtype=pl.Int64),
            pl.lit(match["id"]).alias("match_id"),
            pl.lit(match_label(match)).alias("match_name")))
        offset += len(rounds)
    return pl.concat(frames, how="diagonal_relaxed")


def _team_key(row, role):
    name = str(row.get(f"{role}_name") or "").strip()
    return "name:" + name if name and name not in {"队伍 A", "队伍 B", "战队未知"} else f"{row['match_id']}:{role}"


def comparison_teams(rounds):
    result = {"all": "全部队伍"}
    for row in rounds.iter_rows(named=True):
        for role in ("team_a", "team_b"):
            key = _team_key(row, role)
            result[key] = row[f"{role}_name"] if key.startswith("name:") else f"{row['match_name']} · 队伍 {role[-1].upper()}"
    return result


def filter_comparison(rounds, team, side, phase, economy):
    frames = []
    for match in rounds.partition_by("match_id", maintain_order=True):
        for role in ("team_a", "team_b"):
            selected = match if team == "all" else match.filter(pl.Series([
                _team_key(row, role) == team for row in match.iter_rows(named=True)]))
            if not selected.is_empty():
                frames.append(filter_rounds(selected, role, side, phase, economy))
    return (pl.concat(frames, how="diagonal_relaxed").unique("round_id", maintain_order=True).sort("round_id")
            if frames else rounds.head(0))


def comparison_payload(matches, selection, tick_step, radar=None, images=None,
                       alignment="freeze_end", include_utility=False):
    map_name = same_map(matches)
    if not selection or len(selection) > 20 or len(set(selection)) != len(selection):
        raise ValueError("请选择 1–20 个不同回合。")
    by_id = {match["id"]: match for match in matches}
    if any(mid not in by_id for mid, _ in selection):
        raise ValueError("所选比赛不存在。")
    grouped = {}
    for mid, rid in selection:
        grouped.setdefault(mid, []).append(rid)
    parts, total = [], 0
    for mid, ids in grouped.items():
        match = by_id[mid]
        destination = Path(match["destination"])
        manifest_path = destination / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"{match_label(match)} 的比赛缓存尚未准备好。")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if str(manifest["header"].get("map_name") or "").strip().lower() != map_name:
            raise ValueError("比赛与缓存地图不一致，请重新上传分析。")
        events, rounds = load_dataset(destination)
        states = {}
        for rid in ids:
            path = position_cache_path(destination, rid, tick_step)
            if not path.is_file():
                raise FileNotFoundError(f"{match_label(match)} R{rid} 的位置缓存尚未准备好。")
            total += pl.scan_parquet(path).select(pl.len()).collect().item()
            if total > MAX_PLAYBACK_ROWS:
                raise ValueError("所选位置超过 100 万条，请减少回合数或增大采样间隔。")
            states[rid] = read_parquet(path)
        utility_path = destination / "utility-tracks-v1.parquet"
        if include_utility and not utility_path.is_file():
            raise FileNotFoundError(f"{match_label(match)} 的道具缓存尚未准备好。")
        part = playback_payload(states, rounds, tick_step, radar, images if not parts else {}, alignment,
            utility_tracks=read_parquet(utility_path) if include_utility else None,
            events=events if include_utility else None)
        prefix = mid + ":"
        for player in part["players"]:
            player["steamid"] = player["id"]
            player["id"] = prefix + player["id"]
            player["team"] = prefix + player["team"]
            player["teamName"] = match_label(match) + " · " + player["teamName"]
            player["matchId"] = mid
        for row in part["rounds"]:
            row["sourceRoundId"] = row["id"]
            row["label"] = f"{match_label(match)} · R{row['id']}"
            row["id"] = f"{prefix}r{row['id']}"
            row["matchId"] = mid
            for track in row["tracks"]:
                track["player"] = prefix + track["player"]
            for utility in row["utilities"]:
                if utility["player"]:
                    utility["player"] = prefix + utility["player"]
        parts.append(part)
    result = dict(parts[0])
    result["rounds"] = [row for part in parts for row in part["rounds"]]
    result["players"] = [player for part in parts for player in part["players"]]
    result["maxTick"] = max(part["maxTick"] for part in parts)
    result["bounds"] = [min(part["bounds"][i] for part in parts) if i < 2 else max(part["bounds"][i] for part in parts)
                        for i in range(4)]
    result["matches"] = [{"id": mid, "name": by_id[mid]["name"]} for mid in grouped]
    result["mapName"] = map_name
    return result
