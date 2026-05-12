from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader.filesystem import PathTraversalError, RootedFS


def test_safe_path_root(download_root: Path):
    fs = RootedFS(download_root)
    assert fs.safe_path("") == download_root
    assert fs.safe_path("/") == download_root
    assert fs.safe_path(".") == download_root


def test_safe_path_relative(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    assert fs.safe_path("Movies") == download_root / "Movies"
    assert fs.safe_path("/Movies") == download_root / "Movies"
    assert fs.safe_path("Movies/Foo") == download_root / "Movies/Foo"


def test_safe_path_rejects_dotdot(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path("../etc/passwd")
    with pytest.raises(PathTraversalError):
        fs.safe_path("Movies/../../etc/passwd")


def test_safe_path_rejects_absolute_outside(download_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path(str(outside))


def test_safe_path_rejects_symlink_escape(download_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (download_root / "evil").symlink_to(outside)
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path("evil/anything")


def test_safe_path_rejects_nul(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path("Movies\x00.txt")
