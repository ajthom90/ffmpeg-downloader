from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id                     TEXT PRIMARY KEY,
  url                    TEXT NOT NULL,
  selected_variant_url   TEXT,
  selected_variant_label TEXT,
  filename               TEXT NOT NULL,
  output_path            TEXT NOT NULL,
  extension              TEXT NOT NULL,
  codec                  TEXT NOT NULL,
  backend                TEXT NOT NULL DEFAULT 'ffmpeg',
  format_selector        TEXT,
  format_label           TEXT,
  command                TEXT NOT NULL,
  status                 TEXT NOT NULL,
  progress               REAL,
  duration_seconds       REAL,
  current_time_seconds   REAL,
  speed                  TEXT,
  message                TEXT,
  created_at             INTEGER NOT NULL,
  started_at             INTEGER,
  finished_at            INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_created_at_idx ON jobs(created_at DESC);
"""

JOB_COLUMNS = (
    "id",
    "url",
    "selected_variant_url",
    "selected_variant_label",
    "filename",
    "output_path",
    "extension",
    "codec",
    "backend",
    "format_selector",
    "format_label",
    "command",
    "status",
    "progress",
    "duration_seconds",
    "current_time_seconds",
    "speed",
    "message",
    "created_at",
    "started_at",
    "finished_at",
)


class Database:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        # Serialize all access — sqlite3 connections are not fully thread-safe
        # even with check_same_thread=False.
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: Path) -> Database:
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(SCHEMA)
        db = cls(conn)
        db._migrate()
        return db

    def _migrate(self) -> None:
        with self._lock:
            cur = self._conn.execute("PRAGMA table_info(jobs)")
            existing = {row[1] for row in cur.fetchall()}
            alters: list[str] = []
            if "backend" not in existing:
                alters.append("ALTER TABLE jobs ADD COLUMN backend TEXT NOT NULL DEFAULT 'ffmpeg'")
            if "format_selector" not in existing:
                alters.append("ALTER TABLE jobs ADD COLUMN format_selector TEXT")
            if "format_label" not in existing:
                alters.append("ALTER TABLE jobs ADD COLUMN format_label TEXT")
            for sql in alters:
                self._conn.execute(sql)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def insert_job(self, row: dict[str, Any]) -> None:
        cols = ", ".join(JOB_COLUMNS)
        placeholders = ", ".join("?" for _ in JOB_COLUMNS)
        values = tuple(row[c] for c in JOB_COLUMNS)
        with self._lock:
            self._conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", values)

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = (*fields.values(), job_id)
        with self._lock:
            self._conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            r = cur.fetchone()
            return dict(r) if r else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            )
            return [dict(r) for r in cur.fetchall()]

    def delete_job(self, job_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def reconcile_on_startup(self, now: int, retention_days: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET status='failed', message='Interrupted by restart', "
                "finished_at=? WHERE status IN ('queued','running')",
                (now,),
            )
            cutoff = now - retention_days * 86400
            self._conn.execute(
                "DELETE FROM jobs WHERE status IN ('completed','failed','cancelled') "
                "AND finished_at IS NOT NULL AND finished_at < ?",
                (cutoff,),
            )
