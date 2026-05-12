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


def build_multi_input_command(
    *,
    ffmpeg_bin: str,
    video_url: str,
    audio_urls: list[str],
    subtitle_urls: list[str],
    output_path: str,
    codec: str,
    extension: str,
) -> list[str]:
    """Build an ffmpeg argv that combines explicit video + audio + subtitle inputs.

    Each url becomes its own `-i` input; `-map` flags select streams from each
    input into the output. This is needed for HLS masters that split alternate
    audio/subtitle tracks into separate playlists referenced by EXT-X-MEDIA.

    Subtitle codec defaults to `copy` (works in MKV), with a `mov_text`
    fallback for MP4/MOV which can't natively carry SRT/WebVTT.

    Caller is responsible for sanitizing output_path and ensuring the parent
    dir exists.
    """
    if codec not in CODEC_MAP:
        raise UnsupportedCodecError(f"unknown codec: {codec}")
    if not video_url:
        raise ValueError("video_url is required for multi-input")

    for u in (video_url, *audio_urls, *subtitle_urls):
        scheme = urlparse(u).scheme.lower()
        if scheme not in ("http", "https") and scheme != "":
            raise UnsupportedSchemeError(f"unsupported scheme: {scheme}")

    reconnect = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5"]

    argv: list[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "error"]

    def add_input(url: str) -> None:
        if urlparse(url).scheme.lower() in ("http", "https"):
            argv.extend(reconnect)
        argv.extend(["-i", url])

    add_input(video_url)
    for u in audio_urls:
        add_input(u)
    for u in subtitle_urls:
        add_input(u)

    # -map flags: pick video from input 0, each audio from inputs 1..N,
    # each subtitle from inputs N+1..M.
    argv.extend(["-map", "0:v:0"])
    for i in range(len(audio_urls)):
        argv.extend(["-map", f"{1 + i}:a:0"])
    sub_start = 1 + len(audio_urls)
    for i in range(len(subtitle_urls)):
        argv.extend(["-map", f"{sub_start + i}:s:0"])

    # Codecs. For multi-input we always copy video+audio from the chosen
    # streams; the codec dropdown's main purpose in this mode is governing the
    # subtitle codec choice via container compatibility.
    argv.extend(["-c:v", "copy", "-c:a", "copy"])
    if subtitle_urls:
        ext = extension.lower()
        sub_codec = "mov_text" if ext in ("mp4", "mov", "m4a") else "copy"
        argv.extend(["-c:s", sub_codec])

    # HLS-into-MP4 audio fix
    if extension.lower() in ("mp4", "mov", "m4a"):
        argv.extend(["-bsf:a", "aac_adtstoasc"])

    argv.extend(["-progress", "pipe:1", "-nostats", "-y", output_path])
    return argv


def pretty_command(argv: list[str]) -> str:
    """Render argv as a shell-safe single line for storage/display only."""
    return " ".join(shlex.quote(a) for a in argv)
