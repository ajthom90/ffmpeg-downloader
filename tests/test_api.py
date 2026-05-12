from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader import create_app


@pytest.fixture
def app(download_root: Path, data_dir: Path, fake_ffmpeg_path, fake_ffprobe_path):
    a = create_app(
        {
            "DOWNLOAD_ROOT": str(download_root),
            "DATA_DIR": str(data_dir),
            "FFMPEG_BIN": str(fake_ffmpeg_path),
            "FFPROBE_BIN": str(fake_ffprobe_path),
            "MAX_CONCURRENT_JOBS": "2",
            "TESTING": True,
        }
    )
    yield a
    a.extensions["jobs"].shutdown(wait=True)
    a.extensions["db"].close()


@pytest.fixture
def client(app):
    return app.test_client()


def test_browse_root(client, download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    r = client.get("/api/browse?path=")
    assert r.status_code == 200
    body = r.get_json()
    names = [i["name"] for i in body["items"]]
    assert names == ["Movies", "Music"]


def test_browse_rejects_traversal(client):
    r = client.get("/api/browse?path=../etc")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_browse_missing_path_returns_404(client):
    r = client.get("/api/browse?path=does/not/exist")
    assert r.status_code == 404


def test_mkdir(client, download_root: Path):
    r = client.post("/api/mkdir", json={"path": "", "name": "NewLibrary"})
    assert r.status_code == 200
    assert r.get_json() == {"path": "NewLibrary"}
    assert (download_root / "NewLibrary").is_dir()


def test_mkdir_rejects_bad_name(client):
    r = client.post("/api/mkdir", json={"path": "", "name": ".."})
    assert r.status_code == 400


def test_mkdir_requires_json(client):
    r = client.post("/api/mkdir", data="nope")
    assert r.status_code == 400


def test_validate_existing(client, download_root: Path):
    (download_root / "Movies").mkdir()
    r = client.get("/api/validate?path=Movies")
    body = r.get_json()
    assert body["exists"] is True
    assert body["is_dir"] is True
    assert body["writable"] is True


def test_validate_missing_ok(client):
    r = client.get("/api/validate?path=DoesNotExist/Yet")
    assert r.status_code == 200
    body = r.get_json()
    assert body["exists"] is False
    assert body["writable"] is True


def test_validate_traversal_400(client):
    r = client.get("/api/validate?path=../etc")
    assert r.status_code == 400


def test_autocomplete(client, download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    r = client.get("/api/autocomplete?prefix=Mu")
    assert r.status_code == 200
    body = r.get_json()
    assert [m["name"] for m in body["matches"]] == ["Music"]


def test_search_finds_match(client, download_root: Path):
    (download_root / "Movies" / "Office Space").mkdir(parents=True)
    r = client.get("/api/search?q=office&limit=5")
    body = r.get_json()
    assert [m["path"] for m in body["matches"]] == ["Movies/Office Space"]
    assert body["truncated"] is False
