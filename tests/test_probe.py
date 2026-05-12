from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from ffmpeg_downloader.probe import (
    ProbeResult,
    UnsupportedSchemeError,
    classify,
    fetch_url,
    parse_master_playlist,
    probe_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_classify_master_playlist():
    body = (FIXTURES / "master-simple.m3u8").read_text()
    assert classify(body) == "hls_master"


def test_classify_media_playlist():
    body = (FIXTURES / "media-only.m3u8").read_text()
    assert classify(body) == "hls_media"


def test_classify_not_hls():
    assert classify("not a playlist at all") == "direct"
    assert classify("") == "unknown"


def test_classify_bom_prefixed():
    body = "﻿" + (FIXTURES / "master-simple.m3u8").read_text()
    assert classify(body) == "hls_master"


def test_parse_master_returns_sorted_variants():
    body = (FIXTURES / "master-simple.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://cdn.example.com/master.m3u8")
    bws = [v["bandwidth"] for v in variants]
    assert bws == sorted(bws, reverse=True)
    assert bws == [5_000_000, 2_800_000, 1_400_000]
    assert variants[0]["width"] == 1920 and variants[0]["height"] == 1080
    assert variants[0]["codecs"] == "avc1.640028,mp4a.40.2"
    assert variants[0]["url"] == "https://cdn.example.com/v/1080.m3u8"
    assert variants[0]["label"] == "1920×1080 5.0 Mbps"


def test_parse_master_resolves_relative_uris():
    body = (FIXTURES / "master-relative-uris.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://cdn.example.com/master.m3u8")
    urls = [v["url"] for v in variants]
    assert urls == [
        "https://cdn.example.com/1080/index.m3u8",
        "https://cdn.example.com/360/index.m3u8",
    ]


def test_parse_master_propagates_query_to_relative_variants():
    """Auth-token-on-master providers (Nebula, signed CDNs) put the token on
    the master playlist URL and expect ffmpeg to inherit it onto relative
    variant URIs. Our parser must do the same."""
    body = (FIXTURES / "master-relative-uris.m3u8").read_text()
    variants = parse_master_playlist(
        body, base_url="https://cdn.example.com/master.m3u8?token=abc123&v=1"
    )
    urls = [v["url"] for v in variants]
    assert urls == [
        "https://cdn.example.com/1080/index.m3u8?token=abc123&v=1",
        "https://cdn.example.com/360/index.m3u8?token=abc123&v=1",
    ]


def test_parse_master_does_not_override_variant_query():
    body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360\nv/360.m3u8?own=yes\n"
    variants = parse_master_playlist(body, base_url="https://x.com/m.m3u8?token=abc")
    assert variants[0]["url"] == "https://x.com/v/360.m3u8?own=yes"


def test_parse_master_handles_missing_resolution():
    body = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000,CODECS="mp4a.40.2"\naudio.m3u8\n'
    variants = parse_master_playlist(body, base_url="https://x.com/master.m3u8")
    assert len(variants) == 1
    assert variants[0]["width"] is None and variants[0]["height"] is None


def test_parse_master_handles_bom():
    body = "﻿" + (FIXTURES / "master-simple.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://x.com/m.m3u8")
    assert len(variants) == 3


class _StubHandler(BaseHTTPRequestHandler):
    body_bytes = b""
    delay = 0.0

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.delay:
            import time

            time.sleep(self.delay)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(self.body_bytes)))
        self.end_headers()
        self.wfile.write(self.body_bytes)

    def log_message(self, *_args):  # silence
        return


@pytest.fixture
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _url_for(server, path="/master.m3u8"):
    host, port = server.server_address
    return f"http://{host}:{port}{path}"


def test_fetch_url_returns_body(stub_server):
    _StubHandler.body_bytes = b"#EXTM3U\n"
    body, final_url = fetch_url(_url_for(stub_server), max_bytes=1024, timeout=5.0)
    assert body == "#EXTM3U\n"
    assert final_url.startswith("http://127.0.0.1:")


def test_fetch_url_caps_body(stub_server):
    _StubHandler.body_bytes = b"x" * 1_000_000  # 1 MB
    body, _ = fetch_url(_url_for(stub_server), max_bytes=1024, timeout=5.0)
    assert len(body) == 1024


def test_fetch_url_rejects_bad_scheme():
    with pytest.raises(UnsupportedSchemeError):
        fetch_url("file:///etc/passwd", max_bytes=1024, timeout=5.0)


class _RedirectingHandler(BaseHTTPRequestHandler):
    """Handler that redirects /master to /redirected then serves a master playlist."""

    redirect_target = ""
    body_bytes = b""

    def do_GET(self):  # noqa: N802
        if self.path == "/master.m3u8":
            self.send_response(302)
            self.send_header("Location", self.redirect_target)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(self.body_bytes)))
        self.end_headers()
        self.wfile.write(self.body_bytes)

    def log_message(self, *_args):
        return


def test_probe_url_resolves_variants_against_redirect_target():
    """A 302 from the user-supplied master URL to a CDN URL must cause variants
    to be resolved against the CDN URL — matches ffmpeg's HLS resolution."""
    server = HTTPServer(("127.0.0.1", 0), _RedirectingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        _RedirectingHandler.redirect_target = f"http://{host}:{port}/cdn/v/show/all.m3u8"
        _RedirectingHandler.body_bytes = (
            b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360\nvariants/360.m3u8\n"
        )
        result = probe_url(f"http://{host}:{port}/master.m3u8")
        assert result.type == "hls_master"
        assert len(result.variants) == 1
        # The variant URL must point at the redirected base (under /cdn/v/show/)
        # not the original /master.m3u8.
        assert result.variants[0]["url"] == (f"http://{host}:{port}/cdn/v/show/variants/360.m3u8")
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_probe_url_classifies_master(stub_server):
    _StubHandler.body_bytes = (FIXTURES / "master-simple.m3u8").read_bytes()
    result = probe_url(_url_for(stub_server))
    assert isinstance(result, ProbeResult)
    assert result.type == "hls_master"
    assert len(result.variants) == 3
    # base_url propagated for relative URI resolution
    assert result.variants[0]["url"].startswith("https://cdn.example.com/")


def test_probe_url_unknown_on_fetch_failure():
    result = probe_url("http://127.0.0.1:1/nope")  # port 1 closed
    assert result.type == "unknown"
    assert result.variants == []
    assert result.message  # some error text
