from __future__ import annotations

import json
from pathlib import Path

from ffmpeg_downloader import ytdlp as y

FIXTURES = Path(__file__).parent / "fixtures"


def test_looks_like_direct_media():
    assert y.looks_like_direct_media("https://cdn.example.com/a/b.m3u8")
    assert y.looks_like_direct_media("https://cdn.example.com/v.mp4")
    assert y.looks_like_direct_media("https://cdn.example.com/v.mp4?token=1")
    assert not y.looks_like_direct_media("https://www.youtube.com/watch?v=abc")
    assert not y.looks_like_direct_media("https://youtu.be/abc")


def test_group_formats_includes_best_heights_audio():
    info = json.loads((FIXTURES / "ytdlp-single-video.json").read_text())
    formats = y.group_formats(info)
    ids = [f["id"] for f in formats]
    assert ids[0] == "best"
    assert "1080" in ids
    assert "720" in ids
    assert "360" in ids
    assert "audio" in ids
    best = formats[0]
    assert best["format_selector"] == y.DEFAULT_FORMAT_SELECTOR
    f1080 = next(f for f in formats if f["id"] == "1080")
    assert "1080" in f1080["format_selector"]
    assert f1080["is_audio_only"] is False
    audio = next(f for f in formats if f["id"] == "audio")
    assert audio["is_audio_only"] is True


def test_js_runtime_args():
    assert y.js_runtime_args("deno") == ["--js-runtimes", "deno"]
    assert y.js_runtime_args("node") == ["--js-runtimes", "node"]
    assert y.js_runtime_args("deno:/usr/local/bin/deno") == [
        "--js-runtimes",
        "deno:/usr/local/bin/deno",
    ]
    assert y.js_runtime_args("") == []
    assert y.js_runtime_args(None) == []
    assert y.js_runtime_args("  ") == []


def test_build_download_argv():
    argv = y.build_download_argv(
        ytdlp_bin="/usr/bin/yt-dlp",
        url="https://www.youtube.com/watch?v=abc",
        format_selector="bv*+ba/b",
        output_path="/downloads/clip.mp4",
        extension="mp4",
    )
    assert argv[0] == "/usr/bin/yt-dlp"
    assert "-f" in argv
    assert "bv*+ba/b" in argv
    assert "-o" in argv
    assert "/downloads/clip.mp4" in argv
    assert "--no-playlist" in argv
    assert "--merge-output-format" in argv
    assert "mp4" in argv
    assert "--js-runtimes" in argv
    assert argv[argv.index("--js-runtimes") + 1] == "deno"


def test_build_download_argv_custom_js_runtime():
    argv = y.build_download_argv(
        ytdlp_bin="/usr/bin/yt-dlp",
        url="https://www.youtube.com/watch?v=abc",
        format_selector="bv*+ba/b",
        output_path="/downloads/clip.mp4",
        extension="mp4",
        js_runtime="node",
    )
    assert argv[argv.index("--js-runtimes") + 1] == "node"


def test_parse_progress_line_percent():
    parsed = y.parse_progress_line("PROGRESS percent=12.5 speed=1.2MiB/s")
    assert parsed == {"percent": 12.5, "speed": "1.2MiB/s"}
    assert y.parse_progress_line("not progress") is None


def test_parse_progress_line_download_fallback():
    parsed = y.parse_progress_line("[download]  45.3% of 10.00MiB at 1.00MiB/s")
    assert parsed == {"percent": 45.3, "speed": None}


def test_extract_info_passes_js_runtime(monkeypatch):
    captured: dict = {}

    def fake_run(argv, **_kwargs):
        captured["argv"] = argv

        class _Proc:
            returncode = 0
            stdout = json.dumps({"title": "x", "_type": "video"})
            stderr = ""

        return _Proc()

    monkeypatch.setattr(y.subprocess, "run", fake_run)
    result = y.extract_info(
        "https://www.youtube.com/watch?v=abc",
        ytdlp_bin="yt-dlp",
        js_runtime="deno",
    )
    assert result["ok"] is True
    argv = captured["argv"]
    assert "--js-runtimes" in argv
    assert argv[argv.index("--js-runtimes") + 1] == "deno"
    assert "--dump-single-json" in argv


def test_extract_info_video(fake_ytdlp_path, monkeypatch):
    monkeypatch.setenv(
        "FAKE_YTDLP_JSON_FILE",
        str(FIXTURES / "ytdlp-single-video.json"),
    )
    result = y.extract_info(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ytdlp_bin=str(fake_ytdlp_path),
    )
    assert result["ok"] is True
    assert result["kind"] == "video"
    assert result["info"]["title"] == "Sample Video Title"


def test_extract_info_playlist(fake_ytdlp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_JSON_FILE", str(FIXTURES / "ytdlp-playlist.json"))
    result = y.extract_info(
        "https://www.youtube.com/playlist?list=PLtest",
        ytdlp_bin=str(fake_ytdlp_path),
    )
    assert result["ok"] is True
    assert result["kind"] == "playlist"


def test_extract_info_failure(fake_ytdlp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_EXIT", "1")
    monkeypatch.setenv("FAKE_YTDLP_STDERR", "ERROR: Private video")
    monkeypatch.setenv("FAKE_YTDLP_JSON_FILE", str(FIXTURES / "ytdlp-single-video.json"))
    result = y.extract_info(
        "https://www.youtube.com/watch?v=x",
        ytdlp_bin=str(fake_ytdlp_path),
    )
    assert result["ok"] is False
    assert "Private" in result["error"]


def test_probe_extractor_video(fake_ytdlp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_JSON_FILE", str(FIXTURES / "ytdlp-single-video.json"))
    body = y.probe_extractor(
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        ytdlp_bin=str(fake_ytdlp_path),
    )
    assert body["type"] == "extractor"
    assert body["title"] == "Sample Video Title"
    assert body["formats"][0]["id"] == "best"
    assert body["variants"] == []


def test_probe_extractor_playlist_unsupported(fake_ytdlp_path, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_JSON_FILE", str(FIXTURES / "ytdlp-playlist.json"))
    body = y.probe_extractor(
        "https://www.youtube.com/playlist?list=PLtest",
        ytdlp_bin=str(fake_ytdlp_path),
    )
    assert body["type"] == "unsupported"
    assert "single video" in body["message"].lower() or "Playlist" in body["message"]
