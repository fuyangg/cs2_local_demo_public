"""Validated browser uploads kept on the analysis server for later round parsing."""

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import tempfile

MAX_UPLOAD_MIB = 2048
MAX_UPLOAD_BYTES = MAX_UPLOAD_MIB * 1024**2
CHUNK_BYTES = 8 * 1024**2
MAX_UPLOAD_BATCH = 50


def upload_name(name: str) -> str:
    # Browser filenames are display text, never a server filesystem location.
    name = Path(name.replace("\\", "/")).name
    if Path(name).suffix.lower() != ".dem":
        raise ValueError("请上传已解压的 CS2 .dem 录像。")
    return name


@contextmanager
def saved_upload(upload, data_dir: Path):
    """Atomically publish content; preserve existing files when a retry fails."""
    name = upload_name(upload.name)
    if getattr(upload, "size", 0) > MAX_UPLOAD_BYTES:
        raise ValueError(f"单个录像不能超过 {MAX_UPLOAD_MIB} MiB。")
    directory = Path(data_dir).resolve() / "uploads"
    directory.mkdir(parents=True, exist_ok=True)
    # Hold through parsing so another session cannot reuse a file we then remove.
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        upload.seek(0)
        fd, temporary = tempfile.mkstemp(dir=directory, suffix=".part")
        temporary = Path(temporary)
        destination, created = None, False
        try:
            digest, size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as output:
                header = upload.read(8)
                if header != b"PBDEMS2\x00":
                    raise ValueError("文件头不是 CS2 录像。请确认已解压，且不是 CS:GO 录像。")
                digest.update(header)
                output.write(header)
                size += len(header)
                for chunk in iter(lambda: upload.read(CHUNK_BYTES), b""):
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise ValueError(f"单个录像不能超过 {MAX_UPLOAD_MIB} MiB。")
                    digest.update(chunk)
                    output.write(chunk)
            destination = directory / f"{digest.hexdigest()}.dem"
            if not destination.exists():
                os.replace(temporary, destination)
                created = True
            yield destination, name
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)
            upload.seek(0)


def analyze_uploads(files, matches: dict, data_dir: Path, progress=None) -> list[dict]:
    """Append successes to this session; one failed upload cannot erase other matches."""
    from cs2ta.pipeline import ingest

    if not files or len(files) > MAX_UPLOAD_BATCH:
        raise ValueError(f"每批请选择 1–{MAX_UPLOAD_BATCH} 份录像。")
    results = []
    for index, upload in enumerate(files):
        if progress:
            progress(index, len(files), upload.name)
        try:
            with saved_upload(upload, data_dir) as (source, name):
                destination, manifest = ingest(source, data_dir)
                mid = manifest["demo_sha256"]
                match = {"id": mid, "name": name, "source": str(source),
                         "destination": str(destination),
                         "map_name": str(manifest["header"].get("map_name") or "").strip().lower(),
                         "manifest": {**manifest, "demo_name": name}}
                matches[mid] = match
            results.append({"name": name, "match_id": mid, "error": None})
        except Exception as error:
            results.append({"name": upload.name, "match_id": None, "error": str(error)})
    if progress:
        progress(len(files), len(files), "完成")
    return results
