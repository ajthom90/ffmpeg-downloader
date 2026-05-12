from __future__ import annotations

from pathlib import Path

from ffmpeg_downloader import create_app


def test_create_app_with_overrides(
    download_root: Path, data_dir: Path, fake_ffmpeg_path, fake_ffprobe_path
):
    app = create_app(
        {
            "DOWNLOAD_ROOT": str(download_root),
            "DATA_DIR": str(data_dir),
            "FFMPEG_BIN": str(fake_ffmpeg_path),
            "FFPROBE_BIN": str(fake_ffprobe_path),
            "MAX_CONCURRENT_JOBS": "2",
            "TESTING": True,
        }
    )
    assert app.config["TESTING"] is True
    assert "config" in app.extensions
    assert "db" in app.extensions
    assert "fs" in app.extensions
    assert "jobs" in app.extensions
    client = app.test_client()
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["root_exists"] is True
    assert body["db_ok"] is True


def test_create_app_uses_env_when_no_overrides(
    download_root: Path, data_dir: Path, fake_ffmpeg_path, fake_ffprobe_path, monkeypatch
):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FFMPEG_BIN", str(fake_ffmpeg_path))
    monkeypatch.setenv("FFPROBE_BIN", str(fake_ffprobe_path))
    app = create_app()
    assert app.extensions["config"].download_root == download_root.resolve()
