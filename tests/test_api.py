from __future__ import annotations

import json as _json
import threading
import time as _time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ffmpeg_downloader import create_app


@pytest.fixture
def app(download_root: Path, data_dir: Path, fake_ffmpeg_path, fake_ffprobe_path, fake_ytdlp_path):
    a = create_app(
        {
            "DOWNLOAD_ROOT": str(download_root),
            "DATA_DIR": str(data_dir),
            "FFMPEG_BIN": str(fake_ffmpeg_path),
            "FFPROBE_BIN": str(fake_ffprobe_path),
            "YTDLP_BIN": str(fake_ytdlp_path),
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


class _ProbeStub(BaseHTTPRequestHandler):
    body_bytes = b""
    content_type = "application/vnd.apple.mpegurl"

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body_bytes)))
        self.end_headers()
        self.wfile.write(self.body_bytes)

    def log_message(self, *_args):
        return


@pytest.fixture
def probe_stub():
    srv = HTTPServer(("127.0.0.1", 0), _ProbeStub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        t.join(timeout=2)


def test_probe_master_playlist(client, probe_stub):
    fixtures = Path(__file__).parent / "fixtures"
    _ProbeStub.body_bytes = (fixtures / "master-simple.m3u8").read_bytes()
    host, port = probe_stub.server_address
    url = f"http://{host}:{port}/master.m3u8"
    r = client.post("/api/probe", json={"url": url})
    assert r.status_code == 200
    body = r.get_json()
    assert body["type"] == "hls_master"
    assert len(body["variants"]) == 3
    assert body["variants"][0]["label"] == "1920×1080 5.0 Mbps"


def test_probe_direct_url(client, probe_stub):
    _ProbeStub.body_bytes = b"<html>not a playlist</html>"
    _ProbeStub.content_type = "text/html"
    host, port = probe_stub.server_address
    url = f"http://{host}:{port}/file"
    r = client.post("/api/probe", json={"url": url})
    body = r.get_json()
    assert body["type"] == "direct"
    assert body["variants"] == []


def test_probe_unsupported_scheme(client):
    r = client.post("/api/probe", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400


def test_probe_requires_url(client):
    r = client.post("/api/probe", json={})
    assert r.status_code == 400


def test_probe_extractor_youtube(client, monkeypatch):
    monkeypatch.setenv(
        "FAKE_YTDLP_JSON_FILE",
        str(Path(__file__).parent / "fixtures" / "ytdlp-single-video.json"),
    )
    r = client.post(
        "/api/probe",
        json={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["type"] == "extractor"
    assert body["title"] == "Sample Video Title"
    assert body["formats"][0]["id"] == "best"


def test_probe_playlist_unsupported(client, monkeypatch):
    monkeypatch.setenv(
        "FAKE_YTDLP_JSON_FILE",
        str(Path(__file__).parent / "fixtures" / "ytdlp-playlist.json"),
    )
    r = client.post(
        "/api/probe",
        json={"url": "https://www.youtube.com/playlist?list=PLtest"},
    )
    body = r.get_json()
    assert body["type"] == "unsupported"


def test_post_ytdlp_download(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_TICKS", "1")
    monkeypatch.setenv("FAKE_YTDLP_SLEEP", "0")
    monkeypatch.setenv("FAKE_YTDLP_EXIT", "0")
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://www.youtube.com/watch?v=abc",
            "filename": "hello",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
            "backend": "ytdlp",
            "format_selector": "bv*+ba/b",
            "format_label": "Best available",
        },
    )
    assert r.status_code == 201
    job = r.get_json()
    assert job["backend"] == "ytdlp"
    _wait_for_job_status(app, job["id"], "completed")


def _wait_for_job_status(app, job_id, status, timeout=8.0):
    db = app.extensions["db"]
    deadline = _time.monotonic() + timeout
    last = None
    while _time.monotonic() < deadline:
        last = db.get_job(job_id)
        if last and last["status"] == status:
            return last
        _time.sleep(0.05)
    raise AssertionError(f"{job_id} did not reach {status}; last = {last}")


def test_post_download_creates_queued_job(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "hello",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
        },
    )
    assert r.status_code == 201
    job = r.get_json()
    assert job["id"].startswith("j_")
    assert job["status"] in ("queued", "running", "completed")
    _wait_for_job_status(app, job["id"], "completed")


def test_post_download_rejects_bad_scheme(client):
    r = client.post(
        "/api/downloads",
        json={
            "url": "file:///etc/passwd",
            "filename": "x",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
        },
    )
    assert r.status_code == 400


def test_post_download_rejects_unknown_codec(client):
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "x",
            "extension": "mp4",
            "codec": "banana",
            "output_folder": "",
        },
    )
    assert r.status_code == 400


def test_post_download_rejects_traversal_output_folder(client):
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "x",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "../etc",
        },
    )
    assert r.status_code == 400


def test_list_downloads(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    for n in range(3):
        r = client.post(
            "/api/downloads",
            json={
                "url": f"https://example.com/{n}.mp4",
                "filename": f"f{n}",
                "extension": "mp4",
                "codec": "copy",
                "output_folder": "",
            },
        )
        _wait_for_job_status(app, r.get_json()["id"], "completed")
    r = client.get("/api/downloads?limit=10")
    body = r.get_json()
    assert len(body) == 3
    assert body[0]["filename"] in ("f0.mp4", "f1.mp4", "f2.mp4")


def test_get_one_download(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "single",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
        },
    )
    job_id = r.get_json()["id"]
    _wait_for_job_status(app, job_id, "completed")
    r2 = client.get(f"/api/downloads/{job_id}")
    assert r2.status_code == 200
    assert r2.get_json()["id"] == job_id


def test_get_unknown_download_404(client):
    r = client.get("/api/downloads/j_nope")
    assert r.status_code == 404


def test_delete_terminal_download(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "del",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
        },
    )
    job_id = r.get_json()["id"]
    _wait_for_job_status(app, job_id, "completed")
    d = client.delete(f"/api/downloads/{job_id}")
    assert d.status_code == 200
    r2 = client.get(f"/api/downloads/{job_id}")
    assert r2.status_code == 404


def test_delete_running_download_cancels(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "50")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.1")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "100.0")
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "cancelme",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
        },
    )
    job_id = r.get_json()["id"]
    _wait_for_job_status(app, job_id, "running")
    d = client.delete(f"/api/downloads/{job_id}")
    assert d.status_code == 200
    _wait_for_job_status(app, job_id, "cancelled")


def _parse_sse(chunk: bytes) -> list[dict]:
    """Parse a chunk of SSE bytes into a list of {event, data} dicts."""
    events = []
    event_name = None
    for raw in chunk.decode("utf-8", errors="replace").splitlines():
        if raw.startswith("event:"):
            event_name = raw[len("event:") :].strip()
        elif raw.startswith("data:"):
            data_str = raw[len("data:") :].strip()
            try:
                data = _json.loads(data_str)
            except _json.JSONDecodeError:
                data = data_str
            events.append({"event": event_name, "data": data})
            event_name = None
        elif raw == "":
            event_name = None
    return events


def test_per_job_sse_stream(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "3")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "10.0")
    r = client.post(
        "/api/downloads",
        json={
            "url": "https://example.com/x.mp4",
            "filename": "sse",
            "extension": "mp4",
            "codec": "copy",
            "output_folder": "",
        },
    )
    job_id = r.get_json()["id"]
    # Reading the streaming response until the connection closes.
    with client.get(f"/api/downloads/{job_id}/events", buffered=False) as resp:
        body = resp.get_data()
    events = _parse_sse(body)
    statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
    assert "completed" in statuses


def test_global_sse_route_exists(client):
    # We just verify the route is wired and content type is correct.
    r = client.get(
        "/api/events",
        headers={"Accept": "text/event-stream"},
        buffered=False,
        follow_redirects=False,
    )
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    r.close()


def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    text = r.get_data(as_text=True)
    assert "FFmpeg Downloader" in text
    assert 'id="downloadForm"' in text
    assert 'id="urlInput"' in text
    assert 'id="outputFolder"' in text
    assert 'id="resolutionGroup"' in text  # hidden by default
