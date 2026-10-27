"""SQLite job queue and match index. Transactions never span parsing work."""

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from cs2ta.backend.contracts import ParseRequest, ParseResult


def now():
    return datetime.now(timezone.utc).isoformat()


class Repository:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            schema = db.execute("PRAGMA user_version").fetchone()[0]
            if schema not in (0, 1):
                raise ValueError(f"不支持的后端数据库版本：{schema}")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS matches (
                    id TEXT PRIMARY KEY, source_uri TEXT NOT NULL,
                    dataset_uri TEXT NOT NULL, demo_name TEXT NOT NULL,
                    map_name TEXT, manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS rounds (
                    match_id TEXT NOT NULL REFERENCES matches(id),
                    round_id INTEGER NOT NULL, record_json TEXT NOT NULL,
                    PRIMARY KEY (match_id, round_id)
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, request_key TEXT NOT NULL,
                    request_json TEXT NOT NULL, match_id TEXT REFERENCES matches(id),
                    status TEXT NOT NULL CHECK(status IN
                        ('queued','running','succeeded','failed','interrupted')),
                    stage TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    result_json TEXT, error TEXT
                );
                CREATE UNIQUE INDEX IF NOT EXISTS active_request ON jobs(request_key)
                    WHERE status IN ('queued', 'running');
                CREATE INDEX IF NOT EXISTS queue_order ON jobs(status, created_at);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _job(row):
        if row is None:
            raise KeyError("任务不存在。")
        job = dict(row)
        job["request"] = json.loads(job.pop("request_json"))
        result = job.pop("result_json")
        job["result"] = json.loads(result) if result else None
        job.pop("request_key")
        return job

    def submit(self, request: ParseRequest, match_id: str | None = None,
               source_revision: str = "") -> dict:
        payload = request.model_dump_json()
        key = hashlib.sha256((payload + "\n" + source_revision).encode()).hexdigest()
        stamp = now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM jobs WHERE request_key=? AND status IN ('queued','running')", (key,)).fetchone()
            if existing:
                return self._job(existing)
            job_id = uuid4().hex
            db.execute("""INSERT INTO jobs
                (id, request_key, request_json, match_id, status, stage, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'queued', 'queued', ?, ?)""",
                       (job_id, key, payload, match_id, stamp, stamp))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def get_job(self, job_id):
        with self.connection() as db:
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def jobs(self, limit=100, offset=0):
        with self.connection() as db:
            return [self._job(row) for row in db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (limit, offset))]

    def claim(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created_at, id LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running', stage='preparing', attempts=attempts+1, updated_at=? WHERE id=?", (now(), row["id"]))
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def progress(self, job_id, stage):
        with self.connection() as db:
            db.execute("UPDATE jobs SET stage=?, updated_at=? WHERE id=? AND status='running'", (stage, now(), job_id))

    def complete(self, job_id: str, result: ParseResult):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if job["status"] != "running":
                raise ValueError("只能完成运行中的任务。")
            match_id = result.manifest["demo_sha256"]
            if job["match_id"] and job["match_id"] != match_id:
                raise ValueError("任务结果与比赛身份不匹配。")
            stamp = now()
            db.execute("""INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET source_uri=excluded.source_uri,
                dataset_uri=excluded.dataset_uri, demo_name=excluded.demo_name,
                map_name=excluded.map_name, manifest_json=excluded.manifest_json,
                updated_at=excluded.updated_at""",
                       (match_id, job["request"]["source_uri"], result.dataset_uri,
                        result.manifest["demo_name"], result.manifest["header"].get("map_name"),
                        json.dumps(result.manifest, ensure_ascii=False), stamp, stamp))
            db.execute("DELETE FROM rounds WHERE match_id=?", (match_id,))
            db.executemany("INSERT INTO rounds VALUES (?, ?, ?)", [
                (match_id, row["round_id"], json.dumps(row, ensure_ascii=False, allow_nan=False)) for row in result.rounds])
            db.execute("""UPDATE jobs SET status='succeeded', stage='succeeded',
                match_id=?, result_json=?, error=NULL, updated_at=? WHERE id=?""",
                       (match_id, result.model_dump_json(), stamp, job_id))

    def fail(self, job_id, error):
        with self.connection() as db:
            db.execute("""UPDATE jobs SET status='failed', stage='failed', error=?,
                updated_at=? WHERE id=? AND status='running'""", (str(error), now(), job_id))

    def recover_interrupted(self):
        # Call only while holding the exclusive local worker lock.
        with self.connection() as db:
            return db.execute("""UPDATE jobs SET status='interrupted', stage='interrupted',
                error='worker 中断；可显式重试并复用已完成缓存。', updated_at=?
                WHERE status='running'""", (now(),)).rowcount

    def retry(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            job = self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
            if job["status"] not in ("failed", "interrupted"):
                raise ValueError("只有失败或中断任务可以重试。")
            try:
                db.execute("""UPDATE jobs SET status='queued', stage='queued', error=NULL,
                    result_json=NULL, updated_at=? WHERE id=?""", (now(), job_id))
            except sqlite3.IntegrityError as error:
                raise ValueError("相同任务已经排队或运行。") from error
            return self._job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    @staticmethod
    def _match(row):
        if row is None:
            raise KeyError("比赛不存在。")
        result = dict(row)
        result["manifest"] = json.loads(result.pop("manifest_json"))
        return result

    def match(self, match_id):
        with self.connection() as db:
            return self._match(db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone())

    def matches(self, limit=100, offset=0):
        with self.connection() as db:
            return [self._match(row) for row in db.execute(
                "SELECT * FROM matches ORDER BY created_at DESC, id LIMIT ? OFFSET ?", (limit, offset))]

    def rounds(self, match_id):
        self.match(match_id)
        with self.connection() as db:
            return [json.loads(row[0]) for row in db.execute(
                "SELECT record_json FROM rounds WHERE match_id=? ORDER BY round_id", (match_id,))]
