"""Read replay data within a bounded working set; leave complete caches on disk."""

from pathlib import Path

import polars as pl

from cs2ta.frames import read_parquet, nulls

LIGHT_MAX_ROUNDS = 4
LIGHT_MAX_ROWS = 200_000


def load_round_index(destination: Path) -> pl.DataFrame:
    if not (destination / "manifest.json").is_file():
        raise ValueError("数据集尚未完成解析。")
    rounds = read_parquet(destination / "rounds.parquet")
    for name in ("round_context.parquet", "round-economy-v1.parquet"):
        path = destination / name
        if path.is_file():
            rounds = rounds.join(read_parquet(path), on="round_id", how="left",
                                 validate="1:1", maintain_order="left")
    return rounds


def check_position_budget(paths: dict, max_rows: int) -> int:
    """Check all file metadata before expanding any positions into Python samples."""
    count = 0
    for label, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(f"{label} 的位置缓存尚未准备好。")
        count += pl.scan_parquet(path).select(pl.len()).collect().item()
    if count > max_rows:
        raise ValueError(f"所选位置共 {count:,} 条，超过当前上限 {max_rows:,} 条"
                         "（常规模式最多 100 万条）。请减少回合或提高采样 Tick 间隔。")
    return count


def read_round_window(path: Path, rounds: pl.DataFrame) -> pl.DataFrame:
    """Push tick predicates into Parquet scanning, including disjoint selected rounds."""
    source = pl.scan_parquet(path)
    if "tick" not in source.collect_schema().names():
        return nulls(source.collect())
    predicate = pl.lit(False)
    for row in rounds.iter_rows(named=True):
        predicate |= pl.col("tick").is_between(int(row["start_tick"]), int(row["end_tick"]))
    return nulls(source.filter(predicate).collect())
