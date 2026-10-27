"""Polars frame helpers; normalize missing floating observations to null."""

import math

import polars as pl


def missing(value) -> bool:
    return value is None or isinstance(value, float) and math.isnan(value)


def nulls(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.with_columns(pl.col(pl.Float32, pl.Float64).fill_nan(None))


def column(frame: pl.DataFrame, name: str) -> pl.Series:
    return frame[name] if name in frame.columns else pl.Series(name, [None] * frame.height)


def read_parquet(path) -> pl.DataFrame:
    return nulls(pl.read_parquet(path))
