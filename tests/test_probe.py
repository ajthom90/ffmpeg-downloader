from __future__ import annotations

from pathlib import Path

from ffmpeg_downloader.probe import classify, parse_master_playlist

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


def test_parse_master_handles_missing_resolution():
    body = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=500000,CODECS="mp4a.40.2"\naudio.m3u8\n'
    variants = parse_master_playlist(body, base_url="https://x.com/master.m3u8")
    assert len(variants) == 1
    assert variants[0]["width"] is None and variants[0]["height"] is None


def test_parse_master_handles_bom():
    body = "﻿" + (FIXTURES / "master-simple.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://x.com/m.m3u8")
    assert len(variants) == 3
