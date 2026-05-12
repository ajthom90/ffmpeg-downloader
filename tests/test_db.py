from __future__ import annotations

import time
from pathlib import Path

from ffmpeg_downloader.db import Database


def _make_job_row(**overrides):
    now = int(time.time())
    base = dict(
        id="j_test01",
        url="https://example.com/master.m3u8",
        selected_variant_url=None,
        selected_variant_label=None,
        filename="video.mp4",
        output_path="Movies/video.mp4",
        extension="mp4",
        codec="copy",
        command="ffmpeg -i ... video.mp4",
        status="queued",
        progress=None,
        duration_seconds=None,
        current_time_seconds=None,
        speed=None,
        message=None,
        created_at=now,
        started_at=None,
        finished_at=None,
    )
    base.update(overrides)
    return base


def test_open_creates_schema(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    # Calling open() twice is idempotent.
    db2 = Database.open(data_dir / "jobs.db")
    db.close()
    db2.close()
    assert (data_dir / "jobs.db").exists()


def test_insert_and_get(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    row = _make_job_row()
    db.insert_job(row)
    fetched = db.get_job("j_test01")
    assert fetched is not None
    assert fetched["url"] == row["url"]
    assert fetched["status"] == "queued"
    db.close()


def test_update_status_and_progress(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row())
    db.update_job("j_test01", status="running", started_at=42, progress=33.3, speed="1.0x")
    j = db.get_job("j_test01")
    assert j["status"] == "running"
    assert j["started_at"] == 42
    assert abs(j["progress"] - 33.3) < 0.001
    assert j["speed"] == "1.0x"
    db.close()


def test_list_jobs_newest_first(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row(id="j_a", created_at=100))
    db.insert_job(_make_job_row(id="j_b", created_at=200))
    db.insert_job(_make_job_row(id="j_c", created_at=150))
    ids = [j["id"] for j in db.list_jobs(limit=10)]
    assert ids == ["j_b", "j_c", "j_a"]
    db.close()


def test_delete_job(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row())
    db.delete_job("j_test01")
    assert db.get_job("j_test01") is None
    db.close()


def test_reconcile_marks_running_jobs_failed(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    now = 1_000_000_000
    db.insert_job(_make_job_row(id="j_q", status="queued"))
    db.insert_job(_make_job_row(id="j_r", status="running"))
    db.insert_job(_make_job_row(id="j_done", status="completed", finished_at=now - 100))
    db.reconcile_on_startup(now=now, retention_days=30)
    assert db.get_job("j_q")["status"] == "failed"
    assert db.get_job("j_r")["status"] == "failed"
    assert db.get_job("j_done")["status"] == "completed"
    db.close()


def test_reconcile_prunes_old_terminal_jobs(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    now = 1_000_000_000
    old = now - 31 * 86400
    db.insert_job(_make_job_row(id="j_old", status="completed", finished_at=old))
    db.insert_job(_make_job_row(id="j_new", status="completed", finished_at=now - 100))
    db.reconcile_on_startup(now=now, retention_days=30)
    assert db.get_job("j_old") is None
    assert db.get_job("j_new") is not None
    db.close()
