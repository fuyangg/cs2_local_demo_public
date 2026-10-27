"""Guard the Polars boundary, nullable data and previously written cache facts."""

import ast
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import tomllib

import polars as pl
from polars.testing import assert_frame_equal
import pyarrow as pa
import pyarrow.parquet as pq

from cs2ta import pipeline
from cs2ta.analysis import kill_summary, normalize_states
from cs2ta.frames import nulls
from cs2ta.parser_adapter import parser_frame


def test_project_uses_polars_and_keeps_conversion_at_the_parser_boundary():
    root = Path(__file__).resolve().parents[1]
    paths = [root / "app.py", *sorted((root / "src").rglob("*.py")),
             *sorted((root / "tests").rglob("*.py")), *sorted((root / "scripts").rglob("*.py"))]
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] == "pandas" for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                assert (node.module or "").split(".")[0] != "pandas", path
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr != "to_pandas", path
                if node.func.attr == "from_pandas":
                    assert path == root / "src/cs2ta/parser_adapter.py", path
    dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    assert any(dep.startswith("polars==") for dep in dependencies)
    assert not any(dep.startswith("pandas") for dep in dependencies)


def test_native_frames_keep_uint64_ids_and_normalize_nan_without_mutation():
    source = pl.DataFrame({"steamid": pl.Series([18446744073709551614, None], dtype=pl.UInt64),
                           "x": [float("nan"), 12.]})
    before = source.clone()
    result = parser_frame(source)
    assert isinstance(result, pl.DataFrame)
    assert result["steamid"].to_list() == [18446744073709551614, None]
    assert result["x"].to_list() == [None, 12.]
    assert_frame_equal(source, before)


def test_missing_player_and_event_ids_do_not_lose_integer_precision():
    identifier = 18446744073709551614
    events = pl.DataFrame({"event_type": ["player_death"] * 2, "tick": [1, 1],
                           "attacker_steamid": pl.Series([identifier, None], dtype=pl.UInt64)})
    result = kill_summary(events)
    assert result["steamid"].to_list() == [str(identifier)]
    assert result["kills"].to_list() == [1]
    states = pl.DataFrame({"tick": [1], "steamid": pl.Series([identifier], dtype=pl.UInt64),
                           "X": [float("nan")], "Y": [1.], "Z": [2.], "team_num": [2], "is_alive": [True]})
    normalized = normalize_states(states)
    assert normalized["steamid"].to_list() == [str(identifier)]
    assert normalized["x"].to_list() == [None]


def test_arrow_cache_is_reused_without_rewriting_or_loading_the_parser(tmp_path, monkeypatch):
    source = tmp_path / "legacy.dem"
    source.write_bytes(b"PBDEMS2\x00fixture")
    destination = tmp_path / "data" / f"{pipeline.fingerprint(source)}-{version('demoparser2')}-{pipeline.SCHEMA_VERSION}"
    destination.mkdir(parents=True)
    tables = {
        "events.parquet": pa.table({"tick": [1, 2], "event_type": ["round_start", "round_end"],
                                     "user_steamid": pa.array([18446744073709551614, None], type=pa.uint64())}),
        "rounds.parquet": pa.table({"round_id": [1], "start_tick": [1], "freeze_end_tick": pa.array([None], type=pa.int64()),
                                     "end_tick": [2], "warmup": pa.array([None], type=pa.bool_())}),
        "round_context.parquet": pa.table({"round_id": [1], "team_a_side": pa.array([None], type=pa.int64())}),
        "round-economy-v1.parquet": pa.table({"round_id": [1], "team_a_buy_type": ["unknown"]}),
    }
    for name, table in tables.items():
        pq.write_table(table, destination / name, compression="snappy")
    (destination / "manifest.json").write_text(json.dumps({"demo_sha256": pipeline.fingerprint(source)}))
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in destination.iterdir()}

    def unexpected_parser(*args, **kwargs):
        raise AssertionError("Cached import must not start a parser")

    monkeypatch.setattr(pipeline, "DemoParser", unexpected_parser)
    reused, _ = pipeline.ingest(source, tmp_path / "data")
    assert reused == destination
    events, rounds = pipeline.load_dataset(destination)
    assert isinstance(events, pl.DataFrame) and isinstance(rounds, pl.DataFrame)
    assert events["user_steamid"].to_list() == [18446744073709551614, None]
    assert rounds["freeze_end_tick"].to_list() == [None]
    assert rounds["team_a_buy_type"].to_list() == ["unknown"]
    assert before == {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in before}


def test_empty_frame_keeps_its_schema():
    frame = pl.DataFrame(schema={"tick": pl.Int64, "x": pl.Float64})
    assert_frame_equal(nulls(frame), frame)
