"""Local-only HTTP entry point. Requests enqueue work, never run parsers."""

import argparse
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from cs2ta.backend import development_settings
from cs2ta.backend.contracts import ParseRequest, RoundId
from cs2ta.backend.repository import Repository
from cs2ta.backend.storage import LocalArtifactStore


class ImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_path: str = Field(min_length=1, max_length=4096)


class PositionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round_ids: list[RoundId] = Field(min_length=1, max_length=20)
    tick_step: int = Field(default=16, strict=True, ge=1, le=1024)


def create_app(root: Path | None = None) -> FastAPI:
    from cs2ta import pipeline
    from cs2ta.economy import ECONOMY_LABELS
    from cs2ta.playback import playback_payload
    from cs2ta.radar import RADARS, radar_path
    from cs2ta.round_context import filter_rounds
    import polars as pl
    from cs2ta.frames import read_parquet

    runtime = development_settings(root)
    repository = Repository(runtime.data_dir / "backend-v1.sqlite3")
    storage = LocalArtifactStore(runtime.data_dir)
    app = FastAPI(title="CS2 本地分析 API", version="0.1.0")
    app.state.repository, app.state.storage = repository, storage
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"])

    @app.middleware("http")
    async def local_browser_access(request: Request, call_next):
        # Binding loopback alone does not stop malicious webpages sending requests.
        origin = request.headers.get("origin")
        if origin:
            parts = urlsplit(origin)
            if parts.scheme != request.url.scheme or parts.netloc != request.url.netloc:
                return JSONResponse({"detail": "仅允许同源本地访问。"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "不接受跨站请求。"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(KeyError)
    async def missing(request, error):
        return JSONResponse({"detail": str(error.args[0])}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=422)

    def submit(request: ParseRequest, match_id=None):
        with storage.source(request.source_uri) as source:
            source = pipeline.demo_path(source)
            stat = source.stat()
        revision = f"{stat.st_size}:{stat.st_mtime_ns}"
        return repository.submit(request, match_id, revision)

    def selected_rounds(match_id, round_ids):
        rows = repository.rounds(match_id)
        if not set(round_ids).issubset({row["round_id"] for row in rows}):
            raise KeyError("所选回合不存在。")

    @app.get("/health")
    def health():
        return {"status": "ok", "environment": runtime.environment,
                "worker": "separate-process", "contract": "parse-task-v1"}

    @app.post("/api/imports", status_code=202)
    def import_demo(body: ImportRequest):
        source = pipeline.demo_path(body.source_path)
        return submit(ParseRequest(kind="ingest", source_uri=source.as_uri()))

    @app.get("/api/jobs")
    def jobs(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        return repository.jobs(limit, offset)

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return repository.get_job(job_id)

    @app.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry(job_id: str):
        try:
            return repository.retry(job_id)
        except ValueError as error:
            return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/api/matches")
    def matches(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0)):
        return repository.matches(limit, offset)

    @app.get("/api/matches/{match_id}")
    def match(match_id: str):
        return repository.match(match_id)

    @app.get("/api/matches/{match_id}/rounds")
    def rounds(match_id: str, team: Literal["team_a", "team_b"] = "team_a",
               side: Literal["all", "2", "3"] = "all",
               phase: Literal["all", "regulation", "overtime"] = "all", economy: str = "all"):
        if economy not in ECONOMY_LABELS:
            raise ValueError("未知经济筛选条件。")
        rows = pl.DataFrame(repository.rounds(match_id), infer_schema_length=None)
        filtered = filter_rounds(rows, team, side, phase, economy)
        return [{"match_id": match_id, **row} for row in filtered.to_dicts()]

    @app.post("/api/matches/{match_id}/positions", status_code=202)
    def positions(match_id: str, body: PositionRequest):
        match = repository.match(match_id)
        selected_rounds(match_id, body.round_ids)
        return submit(ParseRequest(kind="positions", source_uri=match["source_uri"],
                                   dataset_uri=match["dataset_uri"], round_ids=body.round_ids,
                                   tick_step=body.tick_step), match_id)

    @app.post("/api/matches/{match_id}/utility", status_code=202)
    def utility(match_id: str):
        match = repository.match(match_id)
        return submit(ParseRequest(kind="utility", source_uri=match["source_uri"],
                                   dataset_uri=match["dataset_uri"]), match_id)

    @app.get("/api/matches/{match_id}/playback")
    def playback(match_id: str,
                 round_ids: Annotated[list[Annotated[int, Field(ge=1)]], Query(min_length=1, max_length=20)],
                 tick_step: int = Query(16, ge=1, le=1024),
                 alignment: Literal["freeze_end", "round_start"] = "freeze_end",
                 include_utility: bool = False):
        match = repository.match(match_id)
        selected_rounds(match_id, round_ids)
        with storage.dataset(match["dataset_uri"]) as destination:
            paths = {rid: pipeline.position_cache_path(destination, rid, tick_step) for rid in round_ids}
            utility_path = destination / "utility-tracks-v1.parquet"
            if (not (destination / "manifest.json").is_file()
                    or any(not path.is_file() for path in paths.values())
                    or (include_utility and not utility_path.is_file())):
                return JSONResponse({"detail": "缓存尚未准备好，请先提交对应解析任务。"}, status_code=409)
            events, round_frame = pipeline.load_dataset(destination)
            spec = RADARS.get(match["map_name"])
            images = {}
            if spec:
                layers = ["upper", "lower"] if spec.lower_max_z is not None else ["upper"]
                images = {layer: radar_path(spec, layer, runtime.map_cache_dir) for layer in layers}
                if any(not path.is_file() for path in images.values()):
                    spec, images = None, {}
            payload = playback_payload(
                {rid: read_parquet(path) for rid, path in paths.items()}, round_frame,
                tick_step, spec, images, alignment,
                utility_tracks=read_parquet(utility_path) if include_utility else None,
                events=events if include_utility else None)
            return {"match_id": match_id, **payload}

    return app


def main() -> int:
    import uvicorn

    parser = argparse.ArgumentParser(description="启动仅本机可访问的开发 API。")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("端口需为 1024–65535。")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
