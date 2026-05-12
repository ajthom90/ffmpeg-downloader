from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader.filesystem import InvalidNameError, PathTraversalError, RootedFS


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


def _seed(root: Path) -> None:
    (root / "Movies").mkdir()
    (root / "Movies" / "Office Space (1999)").mkdir()
    (root / "Movies" / "10 Things").mkdir()
    (root / "Music").mkdir()
    (root / "README.txt").write_text("hi")


def test_browse_root_lists_top_level(download_root: Path):
    _seed(download_root)
    fs = RootedFS(download_root)
    result = fs.browse("")
    assert result["current_path"] == ""
    names = [i["name"] for i in result["items"]]
    # directories first, then files; case-insensitive sort within each group
    assert names == ["Movies", "Music", "README.txt"]
    movies = next(i for i in result["items"] if i["name"] == "Movies")
    assert movies["is_dir"] is True
    assert movies["path"] == "Movies"


def test_browse_subdirectory(download_root: Path):
    _seed(download_root)
    fs = RootedFS(download_root)
    result = fs.browse("Movies")
    assert result["current_path"] == "Movies"
    names = [i["name"] for i in result["items"]]
    assert names == ["10 Things", "Office Space (1999)"]


def test_browse_rejects_traversal(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.browse("../etc")


def test_browse_missing_path_raises_filenotfound(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(FileNotFoundError):
        fs.browse("does/not/exist")


def test_mkdir_creates_subfolder(download_root: Path):
    fs = RootedFS(download_root)
    new_rel = fs.mkdir("", "NewLibrary")
    assert new_rel == "NewLibrary"
    assert (download_root / "NewLibrary").is_dir()


def test_mkdir_creates_nested(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    new_rel = fs.mkdir("Movies", "Foo (2024)")
    assert new_rel == "Movies/Foo (2024)"
    assert (download_root / "Movies" / "Foo (2024)").is_dir()


def test_mkdir_rejects_separators(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "with/slash")
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "with\\backslash")


def test_mkdir_rejects_dot_names(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(InvalidNameError):
        fs.mkdir("", ".")
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "..")
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "")


def test_mkdir_idempotent_returns_existing(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    out = fs.mkdir("", "Movies")
    assert out == "Movies"


def test_validate_existing_directory(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    v = fs.validate("Movies")
    assert v["exists"] is True
    assert v["is_dir"] is True
    assert v["resolved_path"] == "Movies"
    assert v["writable"] is True


def test_validate_existing_file(download_root: Path):
    (download_root / "song.mp3").write_text("x")
    fs = RootedFS(download_root)
    v = fs.validate("song.mp3")
    assert v["exists"] is True
    assert v["is_dir"] is False


def test_validate_missing_path_reports_ancestor_writable(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    v = fs.validate("Movies/NewFolder/SubNew")
    assert v["exists"] is False
    assert v["is_dir"] is False
    assert v["writable"] is True


def test_validate_traversal_raises(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.validate("../etc")


def test_validate_root_is_writable(download_root: Path):
    fs = RootedFS(download_root)
    v = fs.validate("")
    assert v["exists"] is True
    assert v["is_dir"] is True
    assert v["writable"] is True


def test_autocomplete_empty_prefix_lists_root(download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    (download_root / "TVShows").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("")
    names = [m["name"] for m in matches]
    assert names == ["Movies", "Music", "TVShows"]


def test_autocomplete_filters_current_segment(download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Mo")
    assert [m["name"] for m in matches] == ["Movies"]


def test_autocomplete_drills_into_subfolder(download_root: Path):
    (download_root / "Movies" / "Office Space (1999)").mkdir(parents=True)
    (download_root / "Movies" / "October Sky").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Movies/Of")
    names = [m["name"] for m in matches]
    assert names == ["Office Space (1999)"]
    paths = [m["path"] for m in matches]
    assert paths == ["Movies/Office Space (1999)"]


def test_autocomplete_trailing_slash_lists_children(download_root: Path):
    (download_root / "Movies" / "Office").mkdir(parents=True)
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Movies/")
    assert [m["name"] for m in matches] == ["Office"]


def test_autocomplete_cap_10(download_root: Path):
    for i in range(20):
        (download_root / f"Folder{i:02d}").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Folder")
    assert len(matches) == 10
