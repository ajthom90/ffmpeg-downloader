from __future__ import annotations

import time
from pathlib import Path

import pytest

from ffmpeg_downloader.db import Database
from ffmpeg_downloader.filesystem import RootedFS
from ffmpeg_downloader.jobs import JobManager, JobSpec


@pytest.fixture
def db(data_dir: Path):
    d = Database.open(data_dir / "jobs.db")
    yield d
    d.close()


@pytest.fixture
def fs(download_root: Path):
    return RootedFS(download_root)


@pytest.fixture
def jm(db, fs, fake_ffmpeg_path, fake_ffprobe_path):
    manager = JobManager(
        db=db,
        fs=fs,
        ffmpeg_bin=str(fake_ffmpeg_path),
        ffprobe_bin=str(fake_ffprobe_path),
        max_concurrent_jobs=2,
    )
    try:
        yield manager
    finally:
        manager.shutdown(wait=True)


def test_submit_persists_job_as_queued(jm: JobManager, db: Database, download_root: Path):
    # Avoid the executor actually running the job in this first test.
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(
        JobSpec(
            url="https://example.com/m.m3u8",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="my video",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    assert job["id"].startswith("j_")
    assert job["status"] == "queued"
    persisted = db.get_job(job["id"])
    assert persisted is not None
    assert persisted["url"] == "https://example.com/m.m3u8"
    assert persisted["output_path"] == "my video.mp4"
    assert persisted["command"].startswith(str(jm._ffmpeg_bin))


def test_submit_sanitizes_filename(jm: JobManager):
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="bad/name*.mp4",  # already has extension; should be stripped
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    assert job["output_path"] == "badname.mp4"


def test_submit_handles_filename_collision(jm: JobManager, download_root: Path):
    (download_root / "video.mp4").write_text("x")
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="video",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    assert job["output_path"] == "video (2).mp4"


def test_submit_creates_missing_output_folder(jm: JobManager, download_root: Path):
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="video",
            extension="mp4",
            codec="copy",
            output_folder="Movies/NewSubfolder",
        )
    )
    assert (download_root / "Movies" / "NewSubfolder").is_dir()
    assert job["output_path"] == "Movies/NewSubfolder/video.mp4"


def test_submit_uses_variant_url_when_provided(jm: JobManager, db: Database):
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(
        JobSpec(
            url="https://example.com/master.m3u8",
            selected_variant_url="https://example.com/1080.m3u8",
            selected_variant_label="1920×1080 5.0 Mbps",
            filename="video",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    persisted = db.get_job(job["id"])
    assert persisted["selected_variant_url"] == "https://example.com/1080.m3u8"
    # The argv should reference the variant URL, not the master.
    assert "1080.m3u8" in persisted["command"]
    assert "master.m3u8" not in persisted["command"]


def _wait_for_status(db, job_id, status, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = db.get_job(job_id)
        if last and last["status"] == status:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"job {job_id} did not reach status {status!r} within {timeout}s; last={last}"
    )


def test_run_job_completes_successfully(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "2")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.02")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "5.0")
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="ok",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    done = _wait_for_status(db, job["id"], "completed")
    # Either 100 (set at finalize) or the last parsed % — both acceptable.
    assert done["progress"] is not None
    assert done["finished_at"] is not None
    assert done["duration_seconds"] == 5.0


def test_run_job_failure_records_message(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "1")
    monkeypatch.setenv("FAKE_FFMPEG_STDERR", "boom: network error\n")
    monkeypatch.delenv("FAKE_FFPROBE_DURATION", raising=False)  # ffprobe fails → no duration
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="fail",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    failed = _wait_for_status(db, job["id"], "failed")
    assert "boom" in (failed["message"] or "")
    assert failed["duration_seconds"] is None


def test_run_job_updates_progress(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "4")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "10.0")
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="p",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    done = _wait_for_status(db, job["id"], "completed", timeout=10)
    assert done["current_time_seconds"] is not None
    assert done["current_time_seconds"] >= 4.0  # 4 ticks at 1s each


import queue  # noqa: E402


def test_pubsub_delivers_progress_and_status_events(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "2")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "10.0")
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="pubsub",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    q: queue.Queue = jm.subscribe(job["id"])
    seen_status = []
    seen_progress = 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            ev = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if ev["event"] == "status":
            seen_status.append(ev["data"]["status"])
            if ev["data"]["status"] in ("completed", "failed", "cancelled"):
                break
        elif ev["event"] == "progress":
            seen_progress += 1
    assert "running" in seen_status
    assert "completed" in seen_status
    assert seen_progress >= 1


def test_global_subscriber_receives_all_jobs(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    q = jm.subscribe_global()
    jm.submit(
        JobSpec(
            url="https://example.com/a.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="a",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    jm.submit(
        JobSpec(
            url="https://example.com/b.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="b",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    job_ids: set[str] = set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(job_ids) < 2:
        try:
            ev = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if ev["event"] == "job" and ev["data"]["status"] == "completed":
            job_ids.add(ev["data"]["id"])
    assert len(job_ids) == 2


def test_cancel_running_job(jm, db, download_root, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "50")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.1")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "100.0")
    job = jm.submit(
        JobSpec(
            url="https://example.com/x.mp4",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="cancel",
            extension="mp4",
            codec="copy",
            output_folder="",
        )
    )
    # Wait until the job is running, then cancel.
    _wait_for_status(db, job["id"], "running", timeout=5)
    jm.cancel(job["id"])
    cancelled = _wait_for_status(db, job["id"], "cancelled", timeout=10)
    assert cancelled["finished_at"] is not None
    # Partial output file should be cleaned up.
    assert not (download_root / "cancel.mp4").exists()


def test_cancel_unknown_job_is_noop(jm):
    jm.cancel("j_doesnotexist")  # must not raise
