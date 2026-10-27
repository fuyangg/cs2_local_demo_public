"""Parse one request, independently of API, queue and deployment provider."""

import argparse
import json
from pathlib import Path
from typing import Callable

from cs2ta import pipeline
from cs2ta.backend import development_settings
from cs2ta.backend.contracts import ParseRequest, ParseResult
from cs2ta.backend.storage import ArtifactStore, LocalArtifactStore


def execute(request: ParseRequest, storage: ArtifactStore,
            progress: Callable[[str], None] = lambda stage: None) -> ParseResult:
    progress("preparing")
    with storage.source(request.source_uri) as source:
        if request.kind == "ingest":
            progress("ingesting")
            destination, manifest = pipeline.ingest(source, storage.cache_root)
            return _result(destination, manifest, storage, {}, progress)
        with storage.dataset(request.dataset_uri) as destination:
            manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
            progress(request.kind)
            if request.kind == "positions":
                frames = pipeline.parse_rounds_positions(source, destination, request.round_ids, request.tick_step)
                counts = {str(rid): len(frame) for rid, frame in frames.items()}
            else:
                counts = {"utility": len(pipeline.parse_utility_tracks(source, destination))}
            return _result(destination, manifest, storage, counts, progress)


def _result(destination, manifest, storage, counts, progress):
    _, rounds = pipeline.load_dataset(destination)
    records = rounds.to_dicts()
    diagnostics = {}
    for name in ("round_context_diagnostics.json", "round-economy-v1-diagnostics.json"):
        path = destination / name
        if path.is_file():
            diagnostics[name] = json.loads(path.read_text(encoding="utf-8"))
    progress("publishing")
    return ParseResult(dataset_uri=storage.publish(destination), manifest=manifest,
                       rounds=records, row_counts=counts, diagnostics=diagnostics)


def main() -> int:
    parser = argparse.ArgumentParser(description="执行一个版本化解析请求，无需 API 或 SQLite。")
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    try:
        runtime = development_settings()
    except ValueError as error:
        parser.error(str(error))
    if args.request.resolve() == args.result.resolve():
        parser.error("请求和结果不能使用同一文件。")
    args.result.parent.mkdir(parents=True, exist_ok=True)
    try:
        request = ParseRequest.model_validate_json(args.request.read_text(encoding="utf-8"))
        result = execute(request, LocalArtifactStore(args.data_dir or runtime.data_dir))
        pipeline._atomic_json(args.result, result.model_dump())
        return 0
    except Exception as error:
        pipeline._atomic_json(args.result, {"version": "parse-result-v1", "status": "failed", "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
