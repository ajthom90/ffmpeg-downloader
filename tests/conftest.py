# tests/conftest.py
from __future__ import annotations

import stat
from pathlib import Path

import pytest


@pytest.fixture
def download_root(tmp_path: Path) -> Path:
    """Temporary directory used as DOWNLOAD_ROOT."""
    root = tmp_path / "downloads"
    root.mkdir()
    return root


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def fake_ffmpeg_path() -> Path:
    """Absolute path to the bash shim used in place of ffmpeg/ffprobe in tests."""
    return Path(__file__).parent / "fake_ffmpeg.sh"


@pytest.fixture
def fake_ffprobe_path() -> Path:
    return Path(__file__).parent / "fake_ffprobe.sh"


@pytest.fixture(autouse=True)
def _ensure_shims_executable(fake_ffmpeg_path: Path, fake_ffprobe_path: Path) -> None:
    for p in (fake_ffmpeg_path, fake_ffprobe_path):
        if p.exists():
            st = p.stat()
            p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
