from __future__ import annotations

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
