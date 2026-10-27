import polars as pl
import pytest

from cs2ta import pipeline


class FakeParser:
    """Contract fixture, not a replacement for real-demo integration testing."""
    calls = 0

    def __init__(self, path):
        pass

    def parse_header(self):
        return {"map_name": "de_dust2"}

    def list_game_events(self):
        return ["round_start", "round_end", "player_death"]

    def parse_events(self, wanted, other):
        FakeParser.calls += 1
        return [
            ("round_start", pl.DataFrame([{"tick": 10, "is_warmup_period": False}], strict=False)),
            ("round_end", pl.DataFrame([{"tick": 50, "winner": 2}], strict=False)),
            ("player_death", pl.DataFrame([{"tick": 30, "attacker_steamid": 76561198000000001, "user_steamid": 76561198000000002}], strict=False)),
        ]

    def parse_ticks(self, wanted, ticks):
        return pl.DataFrame([{"tick": tick, "steamid": "76561198000000001", "name": "test", "X": 1., "Y": 2., "Z": 3., "team_num": 2, "is_alive": True} for tick in ticks], strict=False)


def test_pipeline_cache_preserves_ids_and_rejects_wrong_demo(tmp_path, monkeypatch):
    FakeParser.calls = 0
    monkeypatch.setattr(pipeline, "DemoParser", FakeParser)
    demo = tmp_path / "test.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    destination, manifest = pipeline.ingest(demo, tmp_path / "data")
    assert (destination / "manifest.json").exists()
    events, rounds = pipeline.load_dataset(destination)
    assert events.filter(pl.col("event_type") == "player_death")["attacker_steamid"][0] == "76561198000000001"
    pipeline.ingest(demo, tmp_path / "data")
    assert FakeParser.calls == 1
    states = pipeline.parse_round_positions(demo, destination, 1, 16)
    assert states["tick"].to_list() == [10, 26, 42]
    assert manifest["demo_sha256"] == pipeline.fingerprint(demo)
    demo.write_bytes(b"PBDEMS2\x00different")
    with pytest.raises(ValueError, match="不匹配"):
        pipeline.parse_round_positions(demo, destination, 1)


def test_failed_parse_does_not_publish_manifest(tmp_path, monkeypatch):
    class BrokenParser(FakeParser):
        def parse_events(self, wanted, other):
            raise ValueError("fixture failure")
    monkeypatch.setattr(pipeline, "DemoParser", BrokenParser)
    demo = tmp_path / "broken.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    with pytest.raises(ValueError, match="fixture failure"):
        pipeline.ingest(demo, tmp_path / "data")
    assert not list((tmp_path / "data").rglob("manifest.json"))


def test_file_validation(tmp_path):
    demo = tmp_path / "bad.dem"
    demo.write_bytes(b"not a demo")
    with pytest.raises(ValueError, match="文件头"):
        pipeline.demo_path(demo)


def test_entity_derived_round_events_need_not_be_listed(tmp_path, monkeypatch):
    class EntityDerivedParser(FakeParser):
        def list_game_events(self):
            return ["player_death"]

        def parse_events(self, wanted, other):
            assert "round_start" in wanted and "round_end" in wanted
            return super().parse_events(wanted, other)

    monkeypatch.setattr(pipeline, "DemoParser", EntityDerivedParser)
    demo = tmp_path / "entity-rounds.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    destination, manifest = pipeline.ingest(demo, tmp_path / "data")
    _, rounds = pipeline.load_dataset(destination)
    assert len(rounds) == 1
    assert manifest["listed_events"] == ["player_death"]
    assert manifest["parsed_event_counts"]["round_start"] == 1


def test_actually_missing_round_events_write_diagnostics(tmp_path, monkeypatch):
    import json

    class NoRoundsParser(FakeParser):
        def parse_events(self, wanted, other):
            return [
                ("round_start", pl.DataFrame(strict=False)),
                ("player_death", pl.DataFrame([{"tick": 30}], strict=False)),
            ]

    monkeypatch.setattr(pipeline, "DemoParser", NoRoundsParser)
    demo = tmp_path / "no-rounds.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    with pytest.raises(ValueError, match="实际解析未获得回合事件"):
        pipeline.ingest(demo, tmp_path / "data")
    report = next((tmp_path / "data").rglob("diagnostics.json"))
    assert json.loads(report.read_text())["parsed_event_counts"] == {"player_death": 1}
    assert not list((tmp_path / "data").rglob("manifest.json"))


def test_multiple_rounds_share_one_position_parse_and_reuse_caches(tmp_path, monkeypatch):
    calls = []

    class BatchParser(FakeParser):
        def parse_ticks(self, wanted, ticks):
            if "X" in wanted:
                calls.append(ticks)
            return super().parse_ticks(wanted, ticks)

    monkeypatch.setattr(pipeline, "DemoParser", BatchParser)
    demo = tmp_path / "batch.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    destination, _ = pipeline.ingest(demo, tmp_path / "data")
    pl.DataFrame([
        {"round_id": 1, "start_tick": 10, "end_tick": 50},
        {"round_id": 2, "start_tick": 100, "end_tick": 140},
    ], strict=False).write_parquet(destination / "rounds.parquet")
    result = pipeline.parse_rounds_positions(demo, destination, [1, 2], 16)
    assert len(calls) == 1
    assert result[1]["tick"].to_list() == [10, 26, 42]
    assert result[2]["tick"].to_list() == [100, 116, 132]
    pipeline.parse_rounds_positions(demo, destination, [1, 2], 16)
    assert len(calls) == 1


def test_economy_migrates_old_context_once_without_rewriting_existing_files(tmp_path, monkeypatch):
    from cs2ta.economy import ECONOMY_PROPS
    calls = []

    class EconomyParser:
        def __init__(self, path):
            pass

        def parse_ticks(self, wanted, ticks):
            assert wanted == ECONOMY_PROPS and ticks == [11]
            calls.append(ticks)
            return pl.DataFrame([{
                "tick": 11, "steamid": f"{side}{i}", "team_num": side,
                "current_equip_value": 5500 if side == 2 else 700,
                "cash_spent_this_round": 5000,
                "inventory": ["AWP"] if side == 2 and i < 2 else ["AK-47"],
            } for side in [2,3] for i in range(5)], strict=False)

    monkeypatch.setattr(pipeline, "DemoParser", EconomyParser)
    (tmp_path / "manifest.json").write_text("{}")
    pl.DataFrame([{"tick": 0, "event_type": "round_start"}], strict=False).write_parquet(tmp_path / "events.parquet")
    pl.DataFrame([{"round_id":1,"start_tick":0,"freeze_end_tick":10,"end_tick":100}], strict=False).write_parquet(tmp_path / "rounds.parquet")
    old = tmp_path / "round_context.parquet"
    pl.DataFrame([{"round_id":1,"team_a_side":2,"team_b_side":3}], strict=False).write_parquet(old)
    before = old.read_bytes()
    pipeline.ensure_round_context(tmp_path / "fixture.dem", tmp_path)
    _,rounds = pipeline.load_dataset(tmp_path)
    assert rounds["team_a_buy_type"][0] == "full-buy"
    assert rounds["team_a_double_awp"][0]
    assert rounds["team_b_buy_type"][0] == "eco"
    pipeline.ensure_round_context(tmp_path / "fixture.dem", tmp_path)
    assert len(calls) == 1 and old.read_bytes() == before
