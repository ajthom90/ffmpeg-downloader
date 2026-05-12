from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader.config import Config, ConfigError


def test_config_defaults(download_root: Path, data_dir: Path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    cfg = Config.from_env()
    assert cfg.download_root == download_root.resolve()
    assert cfg.data_dir == data_dir.resolve()
    assert cfg.port == 8000
    assert cfg.max_concurrent_jobs == 2
    assert cfg.job_retention_days == 30
    assert cfg.search_cache_ttl_seconds == 60
    assert cfg.search_result_limit == 50
    assert cfg.ffmpeg_bin == "ffmpeg"
    assert cfg.ffprobe_bin == "ffprobe"


def test_config_overrides(download_root: Path, data_dir: Path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "4")
    monkeypatch.setenv("FFMPEG_BIN", "/usr/local/bin/ffmpeg")
    cfg = Config.from_env()
    assert cfg.port == 9000
    assert cfg.max_concurrent_jobs == 4
    assert cfg.ffmpeg_bin == "/usr/local/bin/ffmpeg"


def test_config_rejects_missing_download_root(tmp_path: Path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setenv("DOWNLOAD_ROOT", str(missing))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="DOWNLOAD_ROOT"):
        Config.from_env()


def test_config_rejects_download_root_that_is_a_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "file.txt"
    f.write_text("hi")
    monkeypatch.setenv("DOWNLOAD_ROOT", str(f))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="not a directory"):
        Config.from_env()


def test_config_creates_data_dir(tmp_path: Path, monkeypatch, download_root: Path):
    new_data = tmp_path / "data-new"
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(new_data))
    cfg = Config.from_env()
    assert new_data.is_dir()
    assert cfg.data_dir == new_data.resolve()
