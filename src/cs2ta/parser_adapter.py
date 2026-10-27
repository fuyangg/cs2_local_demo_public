"""The pinned parser returns third-party frames; convert once at this boundary."""

import polars as pl

from cs2ta.frames import nulls


def parser_frame(value) -> pl.DataFrame:
    if isinstance(value, pl.DataFrame):
        return nulls(value)
    # demoparser2 0.42.0 has no native Polars return option. Business code never
    # receives its Pandas frames; UInt64 IDs are converted before concatenation.
    return nulls(pl.from_pandas(value, include_index=False))
