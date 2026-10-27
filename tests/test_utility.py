from polars.testing import assert_frame_equal
import polars as pl
import pytest

from cs2ta.utility import utility_payload
from cs2ta.radar import RADARS
from cs2ta import pipeline


def flight(entity=7, owner="76561198000000001", kind="CSmokeGrenadeProjectile", ticks=(100, 101, 102), x=0):
    return pl.DataFrame([{"grenade_entity_id": entity, "steamid": owner, "name": "Zed", "grenade_type": kind,
                          "tick": t, "x": x + i, "y": 10., "z": -600.} for i, t in enumerate(ticks)], strict=False)


def event(kind="smokegrenade_detonate", tick=102, entity=7, owner="76561198000000001", x=2):
    return {"event_type": kind, "tick": tick, "entityid": entity, "user_steamid": owner,
            "user_name": "Zed", "x": x, "y": 10., "z": -600.}


def test_observed_flight_detonation_expiry_and_projected_radius():
    tracks = flight()
    before = tracks.clone()
    events = pl.DataFrame([event(), event("smokegrenade_expired", 180)], strict=False)
    data = utility_payload(tracks, events, 90, 200, 100, RADARS["de_nuke"])
    assert len(data) == 1
    item = data[0]
    assert item["confirmed"] and item["effectTick"] == 2 and item["endTick"] == 80
    assert item["player"] == "76561198000000001" and len(item["samples"]) == 3
    assert item["radius"] == pytest.approx(144 / 7)
    assert item["point"][:2] == pytest.approx(RADARS["de_nuke"].project(2, 10), abs=.001)
    assert_frame_equal(tracks, before)


def test_entity_reuse_and_missing_detonation_never_invent_a_landing():
    tracks = pl.concat([flight(ticks=(100, 101, 102)), flight(ticks=(150, 151, 152), x=500)], how="diagonal_relaxed")
    data = utility_payload(tracks, pl.DataFrame([event()], strict=False), 90, 200, 100)
    assert len(data) == 2
    confirmed, incomplete = data
    assert confirmed["confirmed"] and confirmed["point"] == [2., -10., -600.]
    assert not incomplete["confirmed"] and incomplete["effectTick"] is None
    assert incomplete["point"] == [502., -10., -600.] and incomplete["radius"] == 0


def test_different_throwers_and_fire_entities_are_not_falsely_joined():
    tracks = flight(kind="CMolotovProjectile")
    data = utility_payload(tracks, pl.DataFrame([event("inferno_startburn", 103, entity=80),
                                               event("inferno_expire", 160, entity=80)], strict=False), 90, 200, 100)
    assert len(data) == 1 and data[0]["samples"] and data[0]["endTick"] == 60
    data = utility_payload(tracks, pl.DataFrame([event("inferno_startburn", 103, entity=80, owner="different")], strict=False), 90, 200, 100)
    assert len(data) == 2
    assert not next(i for i in data if i["confirmed"])["samples"]


def test_event_without_flight_or_coordinates_remains_explicit():
    e = event("flashbang_detonate", 110)
    e["x"] = float("nan")
    data = utility_payload(None, pl.DataFrame([e], strict=False), 90, 200, 100)
    assert len(data) == 1 and data[0]["point"] is None and data[0]["samples"] == []
    assert data[0]["endTick"] is None


def test_utility_cache_filters_inventory_and_preserves_exact_ids(tmp_path, monkeypatch):
    import json
    calls = []

    class Parser:
        def __init__(self, path):
            pass

        def parse_grenades(self):
            calls.append(True)
            return pl.concat([flight(), flight(kind="CWeaponGlock")], how="diagonal_relaxed")

    monkeypatch.setattr(pipeline, "DemoParser", Parser)
    demo = tmp_path / "fixture.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    (tmp_path / "manifest.json").write_text(json.dumps({"demo_sha256": pipeline.fingerprint(demo)}))
    data = pipeline.parse_utility_tracks(demo, tmp_path)
    assert len(data) == 3 and data["steamid"][0] == "76561198000000001"
    assert_frame_equal(pipeline.parse_utility_tracks(demo, tmp_path), data)
    assert len(calls) == 1
    demo.write_bytes(b"PBDEMS2\x00changed")
    with pytest.raises(ValueError, match="不匹配"):
        pipeline.parse_utility_tracks(demo, tmp_path)


def test_demo_without_projectiles_caches_an_empty_result(tmp_path, monkeypatch):
    import json

    class Parser:
        def __init__(self, path):
            pass

        def parse_grenades(self):
            return pl.DataFrame(strict=False)

    monkeypatch.setattr(pipeline, "DemoParser", Parser)
    demo = tmp_path / "empty.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    (tmp_path / "manifest.json").write_text(json.dumps({"demo_sha256": pipeline.fingerprint(demo)}))
    assert pipeline.parse_utility_tracks(demo, tmp_path).is_empty()
    assert (tmp_path / "utility-tracks-v1.parquet").is_file()
