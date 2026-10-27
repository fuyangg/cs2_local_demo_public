"""One independent local worker, with a process lock and durable job recovery."""

import argparse
from contextlib import contextmanager
import fcntl
import logging
import signal
from threading import Event

from cs2ta.backend import development_settings
from cs2ta.backend.contracts import ParseRequest
from cs2ta.backend.repository import Repository
from cs2ta.backend.storage import ArtifactStore, LocalArtifactStore
from cs2ta.backend.tasks import execute

logger = logging.getLogger(__name__)


@contextmanager
def worker_lock(repository: Repository):
    with repository.path.with_suffix(".worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("此任务库已有 worker 运行；初期只允许串行解析。") from error
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def run_next(repository: Repository, storage: ArtifactStore) -> bool:
    """Caller holds worker_lock; all slow parsing happens outside SQLite."""
    job = repository.claim()
    if job is None:
        return False
    try:
        result = execute(ParseRequest.model_validate(job["request"]), storage,
                         lambda stage: repository.progress(job["id"], stage))
        repository.complete(job["id"], result)
        logger.info("任务 %s 完成", job["id"])
    except Exception as error:
        repository.fail(job["id"], error)
        logger.exception("任务 %s 失败", job["id"])
    return True


def run(repository: Repository, storage: ArtifactStore, *, once=False,
        stop: Event | None = None, poll_seconds=1.0):
    stop = stop or Event()
    with worker_lock(repository):
        recovered = repository.recover_interrupted()
        if recovered:
            logger.warning("已标记 %s 个中断任务，可通过 API 显式重试", recovered)
        while not stop.is_set():
            worked = run_next(repository, storage)
            if once:
                break
            if not worked:
                stop.wait(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="独立串行解析 worker（dev）。")
    parser.add_argument("--once", action="store_true", help="最多执行一个排队任务后退出")
    args = parser.parse_args()
    try:
        runtime = development_settings()
    except ValueError as error:
        parser.error(str(error))
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda signum, frame: stop.set())
    try:
        run(Repository(runtime.data_dir / "backend-v1.sqlite3"),
            LocalArtifactStore(runtime.data_dir), once=args.once, stop=stop)
    except ValueError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
