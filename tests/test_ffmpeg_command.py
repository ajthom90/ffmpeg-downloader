from __future__ import annotations

import pytest

from ffmpeg_downloader.ffmpeg_command import (
    UnsupportedCodecError,
    UnsupportedSchemeError,
    build_command,
    pretty_command,
)


def test_build_command_copy_codec_http():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/master.m3u8",
        output_path="/downloads/Movies/foo.mp4",
        codec="copy",
        extension="mp4",
    )
    assert argv[0] == "ffmpeg"
    assert "-hide_banner" in argv
    assert "-reconnect" in argv
    # input URL after -i
    i_idx = argv.index("-i")
    assert argv[i_idx + 1] == "https://example.com/master.m3u8"
    # codec copy
    assert "-c:v" in argv and "copy" in argv[argv.index("-c:v") + 1 : argv.index("-c:v") + 2]
    # HLS audio bitstream fix is present for .m3u8 + copy
    assert "-bsf:a" in argv
    assert argv[argv.index("-bsf:a") + 1] == "aac_adtstoasc"
    # progress wiring + output path at the end
    assert "-progress" in argv
    assert argv[-1] == "/downloads/Movies/foo.mp4"


def test_build_command_h264_no_hls_bitstream_filter():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/master.m3u8",
        output_path="/out/file.mp4",
        codec="h264",
        extension="mp4",
    )
    assert "-c:v" in argv
    assert argv[argv.index("-c:v") + 1] == "libx264"
    # only copy+m3u8 triggers the HLS bitstream filter
    assert "-bsf:a" not in argv


def test_build_command_audio_only_codecs_strip_video():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/song.mp3",
        output_path="/out/song.mp3",
        codec="mp3",
        extension="mp3",
    )
    assert "-vn" in argv
    assert "-c:v" not in argv
    assert argv[argv.index("-c:a") + 1] == "libmp3lame"


def test_build_command_rejects_non_http_schemes():
    # Strict allowlist: any scheme other than http/https (and empty scheme for
    # local paths) is rejected. ftp, rtsp, gopher, etc. all hit this.
    for bad in ("ftp://example.com/x.mp4", "rtsp://x/y", "gopher://x/y"):
        with pytest.raises(UnsupportedSchemeError):
            build_command(
                ffmpeg_bin="ffmpeg",
                input_url=bad,
                output_path="/out/x.mp4",
                codec="copy",
                extension="mp4",
            )


def test_build_command_no_reconnect_for_local_path():
    # Empty-scheme inputs (local file paths) are accepted but get no -reconnect.
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="/tmp/local-file.mp4",
        output_path="/out/x.mp4",
        codec="copy",
        extension="mp4",
    )
    assert "-reconnect" not in argv


def test_build_command_rejects_unknown_codec():
    with pytest.raises(UnsupportedCodecError):
        build_command(
            ffmpeg_bin="ffmpeg",
            input_url="https://x/x.mp4",
            output_path="/out/x.mp4",
            codec="banana",
            extension="mp4",
        )


def test_build_command_rejects_bad_scheme():
    with pytest.raises(UnsupportedSchemeError):
        build_command(
            ffmpeg_bin="ffmpeg",
            input_url="file:///etc/passwd",
            output_path="/out/x.mp4",
            codec="copy",
            extension="mp4",
        )


def test_pretty_command_quotes_url():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/has space.m3u8",
        output_path="/out/x.mp4",
        codec="copy",
        extension="mp4",
    )
    s = pretty_command(argv)
    assert "'https://example.com/has space.m3u8'" in s
