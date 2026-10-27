"""HTTP/worker integration with synthetic parser facts, no real Demo required."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient
import polars as pl
import pytest

from cs2ta import pipeline
from cs2ta.backend.api import create_app
from cs2ta.backend.contracts import ParseRequest
from cs2ta.backend.repository import Repository
from cs2ta.backend.storage import LocalArtifactStore
from cs2ta.backend.tasks import execute
from cs2ta.backend.worker import run, worker_lock
from cs2ta.round_context import OVERTIME_PROP


class FixtureParser:
    calls = []
    broken = False

    def __init__(self, path):
        self.path = path

    def parse_header(self):
        return {"map_name": "de_dust2"}

    def list_game_events(self):
        return ["round_start", "round_freeze_end", "round_end", "smokegrenade_detonate"]

    def parse_events(self, wanted, other):
        self.calls.append("events")
        if self.broken:
            raise ValueError("synthetic parse failure")
        return [
            ("round_start", pl.DataFrame([{"tick": t, "is_warmup_period": False} for t in (100, 200)], strict=False)),
            ("round_freeze_end", pl.DataFrame([{"tick": t} for t in (116, 216)], strict=False)),
            ("round_end", pl.DataFrame([{"tick": t} for t in (164, 264)], strict=False)),
            ("smokegrenade_detonate", pl.DataFrame([{
                "tick": 140, "entityid": 7, "user_steamid": "21", "x": 10., "y": 20., "z": 30.}], strict=False))]

    def parse_ticks(self, wanted, ticks):
        self.calls.append("positions" if "X" in wanted else "metadata")
        return pl.DataFrame([{
            "tick": tick, "steamid": f"{side}{i}", "name": f"Player {side}{i}",
            "X": float(tick), "Y": 20., "Z": 30., "team_num": side,
            "is_alive": True, "health": 100, "player_color": i,
            "team_clan_name": "G2" if side == 2 else "NAVI", "total_rounds_played": 2,
            "is_warmup_period": False, OVERTIME_PROP: 0,
            "current_equip_value": 5500, "cash_spent_this_round": 5000,
            "start_balance": 6000, "inventory": ["AK-47"]
        } for tick in ticks for side in (2, 3) for i in range(5)], strict=False)

    def parse_grenades(self):
        self.calls.append("utility")
        return pl.DataFrame([{
            "tick": tick, "grenade_entity_id": 7, "grenade_type": "CSmokeGrenadeProjectile",
            "steamid": "21", "name": "Player 21", "x": 10., "y": 20., "z": 30.
        } for tick in range(130, 141)], strict=False)


@pytest.fixture
def backend(tmp_path, monkeypatch):
    monkeypatch.setenv("CS2TA_ENV", "dev")
    FixtureParser.calls = []
    FixtureParser.broken = False
    monkeypatch.setattr(pipeline, "DemoParser", FixtureParser)
    demo = tmp_path / "fixture.dem"
    demo.write_bytes(b"PBDEMS2\x00fixture")
    app = create_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app.state.repository, app.state.storage, demo


def submit_import(client, demo):
    response = client.post("/api/imports", json={"source_path": str(demo)})
    assert response.status_code == 202, response.text
    return response.json()


def import_match(client, repo, storage, demo):
    job = submit_import(client, demo)
    run(repo, storage, once=True)
    completed = client.get(f"/api/jobs/{job['id']}").json()
    assert completed["status"] == "succeeded", completed
    return completed["match_id"]


def test_http_enqueues_only_and_worker_builds_queryable_replay(backend):
    client, repo, storage, demo = backend
    job = submit_import(client, demo)
    assert FixtureParser.calls == []
    assert submit_import(client, demo)["id"] == job["id"]
    assert Repository(repo.path).get_job(job["id"])["status"] == "queued"
    assert client.get("/api/matches").json() == []

    run(repo, storage, once=True)
    done = client.get(f"/api/jobs/{job['id']}").json()
    mid = done["match_id"]
    assert mid == pipeline.fingerprint(demo)
    assert done["attempts"] == 1 and done["error"] is None
    assert client.get(f"/api/matches/{mid}").json()["map_name"] == "de_dust2"
    rounds = client.get(f"/api/matches/{mid}/rounds?side=2&economy=full-buy").json()
    assert len(rounds) == 2 and all(row["match_id"] == mid for row in rounds)
    assert client.get(f"/api/matches/{mid}/rounds?side=3").json() == []

    url = f"/api/matches/{mid}/playback?round_ids=1&round_ids=2&tick_step=16"
    assert client.get(url).status_code == 409
    task = client.post(f"/api/matches/{mid}/positions", json={"round_ids": [1, 2]}).json()
    run(repo, storage, once=True)
    assert repo.get_job(task["id"])["result"]["row_counts"] == {"1": 50, "2": 50}
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["match_id"] == mid
    assert [r["anchor"] for r in response.json()["rounds"]] == [116, 216]
    assert len(response.json()["players"]) == 10
    assert FixtureParser.calls.count("positions") == 1

    assert client.get(url + "&include_utility=true").status_code == 409
    assert client.post(f"/api/matches/{mid}/utility").status_code == 202
    run(repo, storage, once=True)
    assert client.get(url + "&include_utility=true").status_code == 200
    assert FixtureParser.calls.count("utility") == 1


def test_repeat_import_and_distinct_match_rounds_preserve_cache(backend):
    client, repo, storage, demo = backend
    first = import_match(client, repo, storage, demo)
    same = import_match(client, repo, storage, demo)
    assert same == first
    assert FixtureParser.calls.count("events") == 1
    other = demo.with_name("other.dem")
    other.write_bytes(b"PBDEMS2\x00other")
    second = import_match(client, repo, storage, other)
    assert first != second and len(client.get("/api/matches").json()) == 2
    for mid in (first, second):
        rows = client.get(f"/api/matches/{mid}/rounds").json()
        assert [row["round_id"] for row in rows] == [1, 2]
        assert all(row["match_id"] == mid for row in rows)


def test_failure_and_interruption_are_durable_and_explicitly_retryable(backend):
    client, repo, storage, demo = backend
    job = submit_import(client, demo)
    FixtureParser.broken = True
    run(repo, storage, once=True)
    failed = repo.get_job(job["id"])
    assert failed["status"] == "failed" and "synthetic parse failure" in failed["error"]
    assert client.get("/api/matches").json() == []
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 202
    repo.claim()  # Simulate a worker that died before publishing a result.
    run(repo, storage, once=True)
    assert Repository(repo.path).get_job(job["id"])["status"] == "interrupted"
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 202
    FixtureParser.broken = False
    run(repo, storage, once=True)
    assert repo.get_job(job["id"])["status"] == "succeeded"
    assert repo.get_job(job["id"])["attempts"] == 3
    assert client.post(f"/api/jobs/{job['id']}/retry").status_code == 409


def test_concurrent_submission_and_worker_exclusion(backend):
    client, repo, storage, demo = backend
    request = ParseRequest(kind="ingest", source_uri=demo.as_uri())
    with ThreadPoolExecutor(max_workers=6) as pool:
        jobs = list(pool.map(lambda _: repo.submit(request), range(6)))
    assert len({j["id"] for j in jobs}) == 1
    with worker_lock(repo):
        repo.claim()
        with pytest.raises(ValueError, match="已有 worker"):
            run(repo, storage, once=True)
        assert repo.get_job(jobs[0]["id"])["status"] == "running"
    repo.recover_interrupted()
    new = repo.submit(request)
    assert new["id"] != jobs[0]["id"]
    assert client.post(f"/api/jobs/{jobs[0]['id']}/retry").status_code == 409


def test_import_revision_changes_and_bad_inputs(backend):
    client, repo, storage, demo = backend
    first = submit_import(client, demo)
    demo.write_bytes(demo.read_bytes() + b"changed")
    assert submit_import(client, demo)["id"] != first["id"]
    assert client.post("/api/imports", json={"source_path": "/missing.dem"}).status_code == 422
    assert client.post("/api/imports", json={"source_path": str(demo), "extra": True}).status_code == 422
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/matches/missing/rounds").status_code == 404
    run(repo, storage, once=True)  # Consume the earlier revision's queued task.
    mid = import_match(client, repo, storage, demo)
    for body in ({"round_ids": [1, 1]}, {"round_ids": []}, {"round_ids": [True]},
                 {"round_ids": [1], "tick_step": 0}, {"round_ids": [1], "tick_step": 1.5}):
        assert client.post(f"/api/matches/{mid}/positions", json=body).status_code == 422
    assert client.post(f"/api/matches/{mid}/positions", json={"round_ids": [99]}).status_code == 404
    assert client.get(f"/api/matches/{mid}/rounds?economy=typo").status_code == 422


def test_local_api_rejects_cross_origin_and_untrusted_hosts(backend):
    client, _, _, demo = backend
    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}, {"Sec-Fetch-Site": "cross-site"}):
        assert client.post("/api/imports", json={"source_path": str(demo)}, headers=headers).status_code == 403
    assert client.get("/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/health", headers={"Origin": "http://127.0.0.1"}).status_code == 200


def test_executor_uses_artifact_boundary_without_database(backend, tmp_path):
    _, repo, storage, demo = backend
    events = []

    class StagingStore:
        cache_root = storage.cache_root

        @contextmanager
        def source(self, uri):
            assert uri == "object://bucket/demo"
            events.append("download")
            yield demo

        def publish(self, path):
            events.append("upload")
            return "object://bucket/result"

    result = execute(ParseRequest(kind="ingest", source_uri="object://bucket/demo"), StagingStore(), events.append)
    assert result.dataset_uri == "object://bucket/result"
    assert events == ["preparing", "download", "ingesting", "publishing", "upload"]
    assert repo.matches() == [] and repo.jobs() == []


def test_storage_rejects_remote_and_outside_dataset_paths(tmp_path):
    store = LocalArtifactStore(tmp_path / "cache")
    for uri in ("https://example.com/a", "file://server/a", "file:relative", "file:///a?q=x"):
        with pytest.raises(ValueError):
            with store.source(uri):
                pass
    with pytest.raises(ValueError):
        with store.dataset(tmp_path.as_uri()):
            pass
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache/link").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        with store.dataset((tmp_path / "cache/link").as_uri()):
            pass


def test_one_shot_cli_writes_failure_result_without_queue(tmp_path):
    request = tmp_path / "request.json"
    result = tmp_path / "result.json"
    request.write_text(ParseRequest(kind="ingest", source_uri=(tmp_path / "missing.dem").as_uri()).model_dump_json())
    command = [sys.executable, "-m", "cs2ta.backend.tasks", "--request", str(request),
               "--result", str(result), "--data-dir", str(tmp_path / "cache")]
    proc = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True,
                          env={**os.environ, "CS2TA_ENV": "dev"})
    assert proc.returncode == 1, proc.stderr
    assert json.loads(result.read_text())["status"] == "failed"
    assert not list(tmp_path.rglob("*.sqlite3"))


@pytest.mark.parametrize("marked", [False, True])
def test_backend_refuses_prod(tmp_path, monkeypatch, marked):
    monkeypatch.setenv("CS2TA_ENV", "dev" if marked else "prod")
    if marked:
        (tmp_path / ".runtime").mkdir()
        (tmp_path / ".runtime/deployment.json").write_text('{}')
    with pytest.raises(ValueError, match="prod"):
        create_app(tmp_path)
    assert not (tmp_path / "data").exists()
