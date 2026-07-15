from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    fingerprint TEXT NOT NULL UNIQUE,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    marks INTEGER NOT NULL DEFAULT 0,
                    outputs_json TEXT NOT NULL DEFAULT '[]',
                    retained_path TEXT,
                    message TEXT,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    elapsed_seconds REAL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_updated_at ON jobs(updated_at DESC);
                """
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = 'worker stopped before completion',
                    finished_at = ?, updated_at = ?
                WHERE status = 'running'
                """,
                (utc_now(), utc_now()),
            )

    def find_fingerprint(self, fingerprint: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return self._row(row)

    def start(self, fingerprint: str, source_path: Path) -> int:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    fingerprint, source_path, status, started_at, attempts,
                    created_at, updated_at
                ) VALUES (?, ?, 'running', ?, 1, ?, ?)
                ON CONFLICT(fingerprint) DO UPDATE SET
                    source_path = excluded.source_path,
                    status = 'running',
                    marks = 0,
                    outputs_json = '[]',
                    retained_path = NULL,
                    message = NULL,
                    error = NULL,
                    started_at = excluded.started_at,
                    finished_at = NULL,
                    elapsed_seconds = NULL,
                    attempts = jobs.attempts + 1,
                    updated_at = excluded.updated_at
                """,
                (fingerprint, str(source_path), now, now, now),
            )
            row = connection.execute(
                "SELECT id FROM jobs WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def finish(
        self,
        job_id: int,
        status: str,
        *,
        marks: int = 0,
        outputs: list[str] | None = None,
        retained_path: str | None = None,
        message: str | None = None,
        error: str | None = None,
        elapsed_seconds: float | None = None,
    ) -> None:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs SET status = ?, marks = ?, outputs_json = ?,
                    retained_path = ?, message = ?, error = ?, finished_at = ?,
                    elapsed_seconds = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    marks,
                    json.dumps(outputs or [], ensure_ascii=False),
                    retained_path,
                    message,
                    error,
                    now,
                    elapsed_seconds,
                    now,
                    job_id,
                ),
            )

    def latest(self, limit: int = 50) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(row) for row in rows]

    def get(self, job_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row(row)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        value["outputs"] = json.loads(value.pop("outputs_json"))
        return value
