from __future__ import annotations

import shlex
from urllib.parse import urlparse


class UnsupportedCodecError(ValueError):
    pass


class UnsupportedSchemeError(ValueError):
    pass


CODEC_MAP: dict[str, dict] = {
    "copy": {"video": ["-c:v", "copy"], "audio": ["-c:a", "copy"], "extra": []},
    "h264": {
        "video": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "extra": [],
    },
    "h265": {
        "video": ["-c:v", "libx265", "-preset", "veryfast", "-crf", "28"],
        "audio": ["-c:a", "aac", "-b:a", "192k"],
        "extra": [],
    },
    "vp9": {
        "video": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "31"],
        "audio": ["-c:a", "libopus", "-b:a", "128k"],
        "extra": [],
    },
    "aac": {"video": None, "audio": ["-c:a", "aac", "-b:a", "192k"], "extra": ["-vn"]},
    "mp3": {"video": None, "audio": ["-c:a", "libmp3lame", "-q:a", "2"], "extra": ["-vn"]},
}


def build_command(
    *,
    ffmpeg_bin: str,
    input_url: str,
    output_path: str,
    codec: str,
    extension: str,
) -> list[str]:
    """Return the argv list ffmpeg will be invoked with.

    Caller is responsible for sanitizing output_path and ensuring the parent dir exists.
    """
    if codec not in CODEC_MAP:
        raise UnsupportedCodecError(f"unknown codec: {codec}")

    scheme = urlparse(input_url).scheme.lower()
    # Strict allowlist. The API layer already gates this, but enforcing it
    # here keeps build_command's contract self-contained: only http(s) URLs
    # (or empty-scheme local paths, used by tests) ever reach ffmpeg.
    if scheme not in ("http", "https") and scheme != "":
        raise UnsupportedSchemeError(f"unsupported scheme: {scheme}")

    cfg = CODEC_MAP[codec]
    argv: list[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "error"]
    if scheme in ("http", "https"):
        argv += [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
        ]
    argv += ["-i", input_url]

    if cfg["video"] is not None:
        argv += cfg["video"]
    argv += cfg["audio"]
    argv += cfg["extra"]

    # HLS-into-MP4 audio fix: only when input is .m3u8 AND audio is being copied.
    if input_url.lower().split("?", 1)[0].endswith(".m3u8") and codec == "copy":
        argv += ["-bsf:a", "aac_adtstoasc"]

    argv += ["-progress", "pipe:1", "-nostats", "-y", output_path]
    return argv


def pretty_command(argv: list[str]) -> str:
    """Render argv as a shell-safe single line for storage/display only."""
    return " ".join(shlex.quote(a) for a in argv)
