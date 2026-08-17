"""yt-dlp integration: metadata extract, format grouping, download argv, progress."""

from __future__ import annotations

import json
import re
import subprocess
from typing import Any
from urllib.parse import urlparse

DEFAULT_FORMAT_SELECTOR = "bv*+ba/b"
DEFAULT_JS_RUNTIME = "deno"


def js_runtime_args(js_runtime: str | None) -> list[str]:
    runtime = (js_runtime or "").strip()
    if not runtime:
        return []
    return ["--js-runtimes", runtime]


_HEIGHT_BUCKETS = (2160, 1440, 1080, 720, 480, 360)
_MEDIA_EXTS = {
    ".m3u8",
    ".mp4",
    ".webm",
    ".mkv",
    ".mp3",
    ".m4a",
    ".ts",
    ".mov",
    ".avi",
    ".flac",
    ".ogg",
    ".opus",
    ".wav",
}
_PROGRESS_RE = re.compile(r"^PROGRESS\s+percent=(?P<percent>\S+)\s+speed=(?P<speed>\S+)\s*$")
_DOWNLOAD_PCT_RE = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%")


def looks_like_direct_media(url: str) -> bool:
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in _MEDIA_EXTS)


def group_formats(info: dict) -> list[dict]:
    """Build UI quality options from a yt-dlp info dict.

    Include height bucket B when any format has height >= B (a 1080p source
    also offers 720p as a ceiling selector). Always include Best and Audio only.
    """
    raw = info.get("formats") or []
    heights: set[int] = set()
    for f in raw:
        h = f.get("height")
        if isinstance(h, int) and h > 0:
            heights.add(h)

    out: list[dict] = [
        {
            "id": "best",
            "label": "Best available",
            "format_selector": DEFAULT_FORMAT_SELECTOR,
            "height": None,
            "ext": "mp4",
            "is_audio_only": False,
        }
    ]
    for bucket in _HEIGHT_BUCKETS:
        if any(h >= bucket for h in heights):
            out.append(
                {
                    "id": str(bucket),
                    "label": f"{bucket}p",
                    "format_selector": f"bv*[height<={bucket}]+ba/b[height<={bucket}]",
                    "height": bucket,
                    "ext": "mp4",
                    "is_audio_only": False,
                }
            )
    out.append(
        {
            "id": "audio",
            "label": "Audio only",
            "format_selector": "ba/b",
            "height": None,
            "ext": "m4a",
            "is_audio_only": True,
        }
    )
    return out


def build_download_argv(
    *,
    ytdlp_bin: str,
    url: str,
    format_selector: str,
    output_path: str,
    extension: str,
    js_runtime: str = DEFAULT_JS_RUNTIME,
) -> list[str]:
    return [
        ytdlp_bin,
        *js_runtime_args(js_runtime),
        "--no-playlist",
        "--newline",
        "--progress",
        "--progress-template",
        "PROGRESS percent=%(progress._percent_str)s speed=%(progress._speed_str)s",
        "-f",
        format_selector,
        "--merge-output-format",
        extension.lstrip("."),
        "-o",
        output_path,
        "--",
        url,
    ]


def parse_progress_line(line: str) -> dict | None:
    text = line.strip()
    m = _PROGRESS_RE.match(text)
    if m:
        raw_pct = m.group("percent").strip().rstrip("%")
        try:
            percent = float(raw_pct)
        except ValueError:
            return None
        speed = m.group("speed")
        if speed in ("NA", "None", "N/A", ""):
            speed = None
        return {"percent": percent, "speed": speed}
    m2 = _DOWNLOAD_PCT_RE.search(text)
    if m2:
        return {"percent": float(m2.group("percent")), "speed": None}
    return None


def extract_info(
    url: str,
    *,
    ytdlp_bin: str,
    js_runtime: str = DEFAULT_JS_RUNTIME,
    timeout: float = 60.0,
) -> dict[str, Any]:
    argv = [
        ytdlp_bin,
        *js_runtime_args(js_runtime),
        "--skip-download",
        "--dump-single-json",
        "--no-playlist",
        "--",
        url,
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"ok": False, "error": f"yt-dlp not found: {ytdlp_bin}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "yt-dlp timed out"}
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "yt-dlp failed").strip()
        return {"ok": False, "error": err[:500]}
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "yt-dlp returned invalid JSON"}
    kind = "video"
    if info.get("_type") == "playlist" or (
        isinstance(info.get("entries"), list) and len(info["entries"]) > 1
    ):
        kind = "playlist"
    return {"ok": True, "kind": kind, "info": info}


_RESTRICTED_MARKERS = (
    "private video",
    "age-restricted",
    "age restricted",
    "sign in",
    "login required",
    "members only",
    "this video is not available",
    "video unavailable",
    "http error 403",
    "http error 401",
)


def probe_extractor(
    url: str, *, ytdlp_bin: str, js_runtime: str = DEFAULT_JS_RUNTIME
) -> dict[str, Any]:
    result = extract_info(url, ytdlp_bin=ytdlp_bin, js_runtime=js_runtime)
    if not result["ok"]:
        err = result["error"]
        low = err.lower()
        # Access/restriction failures: surface to the user, do not fall through.
        if any(m in low for m in _RESTRICTED_MARKERS):
            return {
                "type": "unsupported",
                "message": err,
                "variants": [],
                "formats": [],
            }
        # No extractor, binary missing, or generic failure → fall through to HLS/direct.
        return {"type": "none", "message": err}
    if result["kind"] == "playlist":
        return {
            "type": "unsupported",
            "message": ("Playlists and channels are not supported. Paste a single video URL."),
            "variants": [],
            "formats": [],
        }
    info = result["info"]
    duration = info.get("duration")
    return {
        "type": "extractor",
        "title": info.get("title") or "",
        "extractor": info.get("extractor") or info.get("ie_key") or "",
        "webpage_url": info.get("webpage_url") or url,
        "duration_seconds": float(duration) if duration is not None else None,
        "formats": group_formats(info),
        "variants": [],
    }
