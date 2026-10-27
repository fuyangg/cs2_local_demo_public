"""Read-only demo parsing with local, content-addressed Polars/Parquet caching."""

import hashlib
import json
import os
import tempfile
from importlib.metadata import version
from pathlib import Path

import polars as pl
from demoparser2 import DemoParser

from cs2ta.analysis import build_rounds, normalize_states
from cs2ta.economy import ECONOMY_PROPS, build_round_economy
from cs2ta.frames import missing, read_parquet
from cs2ta.parser_adapter import parser_frame
from cs2ta.round_context import CONTEXT_PROPS, build_round_context, color_index
from cs2ta.runtime import settings

SCHEMA_VERSION = "mvp-2"
EVENT_NAMES = [
    "round_start", "round_freeze_end", "round_end", "player_death",
    "bomb_planted", "bomb_defused", "bomb_exploded",
    "smokegrenade_detonate", "smokegrenade_expired", "flashbang_detonate",
    "hegrenade_detonate", "inferno_startburn", "inferno_expire",
]
POSITION_VERSION = "v4"
MAX_ROUNDS = 20
ID_COLUMNS = ["user_steamid", "attacker_steamid", "assister_steamid"]


def demo_path(value: str | Path) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"找不到 Demo：{path}。Windows 文件请使用 /mnt/c/... 形式的 WSL 路径。")
    if path.suffix.lower() != ".dem":
        raise ValueError("请提供已解压的 .dem 文件。")
    with path.open("rb") as source:
        if source.read(8) != b"PBDEMS2\x00":
            raise ValueError("文件头不是 CS2 Demo（PBDEMS2）。请确认已解压且不是 CS:GO Demo。")
    return path


def fingerprint(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def _atomic_parquet(path: Path, frame: pl.DataFrame) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".parquet.tmp")
    os.close(fd)
    try:
        frame.write_parquet(name)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def ingest(path: str | Path, data_dir: str | Path | None = None) -> tuple[Path, dict]:
    path = demo_path(path)
    stat_before = path.stat()
    checksum = fingerprint(path)
    parser_version = version("demoparser2")
    destination = Path(data_dir or settings().data_dir).expanduser().resolve() / f"{checksum}-{parser_version}-{SCHEMA_VERSION}"
    destination.mkdir(parents=True, exist_ok=True)
    import fcntl
    with (destination / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest_path = destination / "manifest.json"
        if manifest_path.exists():
            ensure_round_context(path, destination)
            return destination, json.loads(manifest_path.read_text(encoding="utf-8"))
        parser = DemoParser(str(path))
        header = parser.parse_header()
        listed = set(parser.list_game_events())
        # Enumeration can omit events reconstructed from entities.
        parsed = parser.parse_events(EVENT_NAMES, other=["is_warmup_period"])
        frames = []
        for name, raw_frame in parsed:
            frame = parser_frame(raw_frame)
            if frame.is_empty():
                continue
            frame = frame.with_columns(
                pl.lit(name).alias("event_type"),
                *[pl.col(c).cast(pl.String) for c in ID_COLUMNS if c in frame.columns])
            frames.append(frame)
        counts = {frame["event_type"][0]: frame.height for frame in frames}
        diagnostics = {"parser_version": parser_version, "header": header,
                       "listed_events": sorted(listed), "parsed_event_counts": counts}
        absent_rounds = sorted({"round_start", "round_end"} - set(counts))
        if absent_rounds:
            report = destination / "diagnostics.json"
            _atomic_json(report, diagnostics)
            raise ValueError(
                f"实际解析未获得回合事件 {absent_rounds}；已解析事件数量：{counts}。"
                f"诊断报告：{report}。不能仅据此判定 Demo 损坏。")
        events = pl.concat(frames, how="diagonal_relaxed")
        if events.is_empty() or "tick" not in events.columns or events["tick"].null_count():
            raise ValueError("解析未获得有效事件 Tick。")
        events = events.with_columns(pl.col("tick").cast(pl.Int64)).sort("tick", maintain_order=True)
        events = events.with_columns(pl.Series("event_id", [f"e{i}" for i in range(events.height)]))
        rounds = build_rounds(events)
        if rounds.is_empty():
            raise ValueError("未找到完整回合；Demo 可能被截断或仅包含预热。")
        stat_after = path.stat()
        if (stat_before.st_size, stat_before.st_mtime_ns) != (stat_after.st_size, stat_after.st_mtime_ns):
            raise ValueError("解析期间 Demo 文件发生变化，请等待文件复制完成后重试。")
        warnings = [
            "回合编号为录制顺序，可能包含刀局、重启和加时，不等同于比赛比分。",
            "只展示原始 Tick；未假设 Tick 频率或计算活动回合秒数。",
            "道具事件表示爆开/结束等观测，不表示真实覆盖、致盲或战术意图。",
        ]
        manifest = {
            "schema_version": SCHEMA_VERSION, "parser": "demoparser2", "parser_version": parser_version,
            "demo_sha256": checksum, "demo_name": path.name, "header": header,
            "event_count": events.height, "round_count": rounds.height,
            "available_events": sorted(listed | set(counts)),
            "absent_requested_events": sorted(set(EVENT_NAMES) - set(counts)),
            "listed_events": sorted(listed), "parsed_event_counts": counts, "warnings": warnings,
        }
        _atomic_parquet(destination / "events.parquet", events)
        _atomic_parquet(destination / "rounds.parquet", rounds)
        _atomic_json(manifest_path, manifest)
        ensure_round_context(path, destination)
        return destination, manifest


def load_dataset(destination: Path) -> tuple[pl.DataFrame, pl.DataFrame]:
    if not (destination / "manifest.json").is_file():
        raise ValueError("数据集尚未完成解析。")
    events, rounds = read_parquet(destination / "events.parquet"), read_parquet(destination / "rounds.parquet")
    for name in ("round_context.parquet", "round-economy-v1.parquet"):
        path = destination / name
        if path.is_file():
            rounds = rounds.join(read_parquet(path), on="round_id", how="left", validate="1:1", maintain_order="left")
    return events, rounds


def position_cache_path(destination: Path, round_id: int, tick_step: int) -> Path:
    return destination / f"positions-{POSITION_VERSION}-r{round_id}-step{tick_step}.parquet"


def ensure_round_context(path: Path, destination: Path) -> None:
    output = destination / "round_context.parquet"
    if output.is_file():
        ensure_round_economy(path, destination)
        return
    rounds = read_parquet(destination / "rounds.parquet")
    ticks = []
    for row in rounds.iter_rows(named=True):
        freeze = row.get("freeze_end_tick")
        anchor = int(freeze) if not missing(freeze) else int(row["start_tick"])
        ticks.extend([min(anchor + 1, int(row["end_tick"])), min(anchor + 64, int(row["end_tick"]))])
    try:
        states = parser_frame(DemoParser(str(path)).parse_ticks(CONTEXT_PROPS, ticks=sorted(set(ticks))))
        context = build_round_context(rounds, states)
    except Exception as error:
        context = build_round_context(rounds, pl.DataFrame())
        _atomic_json(destination / "round_context_diagnostics.json", {"error": str(error)})
    _atomic_parquet(output, context)
    ensure_round_economy(path, destination)


def parse_rounds_positions(
    path: str | Path, destination: Path, round_ids: list[int], tick_step: int = 16,
) -> dict[int, pl.DataFrame]:
    """Read all uncached selected rounds in one parser pass."""
    if tick_step < 1:
        raise ValueError("tick_step 必须为正整数。")
    if not round_ids or len(set(round_ids)) > MAX_ROUNDS:
        raise ValueError("请选择 1–20 个回合。")
    path = demo_path(path)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if fingerprint(path) != manifest["demo_sha256"]:
        raise ValueError("所选 Demo 与数据集不匹配，请重新解析比赛。")
    _, rounds = load_dataset(destination)
    result, pending, wanted_ticks = {}, {}, []
    for round_id in dict.fromkeys(round_ids):
        selected = rounds.filter(pl.col("round_id") == round_id)
        if selected.is_empty():
            raise ValueError(f"没有回合 {round_id}。")
        row = selected.row(0, named=True)
        ticks = range(int(row["start_tick"]), int(row["end_tick"]) + 1, tick_step)
        if len(ticks) > 20000:
            raise ValueError(f"回合 {round_id} 采样点超过 20000，请增大 Tick 间隔。")
        cached = position_cache_path(destination, round_id, tick_step)
        if cached.exists():
            result[round_id] = read_parquet(cached)
        else:
            pending[round_id] = (row, cached)
            wanted_ticks.extend(ticks)
    if pending:
        frame = parser_frame(DemoParser(str(path)).parse_ticks(
            ["X", "Y", "Z", "team_num", "is_alive", "health", "player_color", "team_clan_name"],
            ticks=sorted(set(wanted_ticks))))
        if frame.is_empty():
            raise ValueError("未获得位置采样，可能是录制缺失；可减小采样间隔后重试。")
        frame = normalize_states(frame)
        if "player_color" not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias("player_color"))
        frame = frame.with_columns(pl.col("player_color").map_elements(color_index, return_dtype=pl.Int64).alias("player_color_id"))
        if not frame["tick"].is_in(wanted_ticks).all():
            raise ValueError("解析器返回了请求采样范围之外的位置。")
        for round_id, (row, cached) in pending.items():
            positions = frame.filter(pl.col("tick").is_between(row["start_tick"], row["end_tick"]))
            if positions.is_empty():
                raise ValueError(f"回合 {round_id} 未获得位置采样。")
            _atomic_parquet(cached, positions)
            result[round_id] = positions
    return result


def parse_round_positions(path: str | Path, destination: Path, round_id: int, tick_step: int = 16) -> pl.DataFrame:
    return parse_rounds_positions(path, destination, [round_id], tick_step)[round_id]


def parse_utility_tracks(path: str | Path, destination: Path) -> pl.DataFrame:
    """Cache projectile observations separately; preserve all older caches."""
    path = demo_path(path)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    if fingerprint(path) != manifest["demo_sha256"]:
        raise ValueError("所选 Demo 与数据集不匹配，请重新解析比赛。")
    output = destination / "utility-tracks-v1.parquet"
    import fcntl
    with (destination / ".utility.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if output.is_file():
            return read_parquet(output)
        frame = parser_frame(DemoParser(str(path)).parse_grenades())
        required = {"grenade_type", "grenade_entity_id", "tick", "x", "y", "z", "steamid"}
        if frame.is_empty():
            frame = pl.DataFrame(schema={"grenade_type": pl.String, "grenade_entity_id": pl.Int64,
                                         "tick": pl.Int64, "x": pl.Float64, "y": pl.Float64,
                                         "z": pl.Float64, "steamid": pl.String, "name": pl.String})
        if not required.issubset(frame.columns):
            raise ValueError("解析器未返回完整的道具轨迹字段。")
        frame = frame.filter(pl.col("grenade_type").cast(pl.String).str.ends_with("Projectile"))
        frame = frame.with_columns(pl.col("steamid").cast(pl.String))
        _atomic_parquet(output, frame)
        return frame


def ensure_round_economy(path: Path, destination: Path) -> None:
    output = destination / "round-economy-v1.parquet"
    if output.is_file():
        return
    _, rounds = load_dataset(destination)
    ticks = [min(int(row["freeze_end_tick"]) + 1, int(row["end_tick"]))
             for row in rounds.iter_rows(named=True) if not missing(row.get("freeze_end_tick"))]
    try:
        states = parser_frame(DemoParser(str(path)).parse_ticks(ECONOMY_PROPS, ticks=sorted(set(ticks)))) if ticks else pl.DataFrame()
        economy = build_round_economy(rounds, states)
    except Exception as error:
        economy = build_round_economy(rounds, pl.DataFrame())
        _atomic_json(destination / "round-economy-v1-diagnostics.json", {"error": str(error)})
    _atomic_parquet(output, economy)
