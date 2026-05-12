from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen

_ATTR_RE = re.compile(r'([A-Z0-9-]+)\s*=\s*(?:"([^"]*)"|([^,]*))(?:,\s*|$)')


def _strip_bom(s: str) -> str:
    return s.lstrip("﻿")


def _resolve_variant_url(base_url: str, uri: str) -> str:
    """Resolve a variant URI against the master playlist URL.

    Standard urljoin loses the query string when joining a bare relative URI to
    a base that has one. Many HLS providers (Nebula, signed-token CDNs, etc.)
    put auth tokens on the master URL and expect them to propagate to variant
    URLs — ffmpeg does this when it resolves variants itself, so we match that.

    Rules:
      - Absolute variant URI → return as-is.
      - Relative variant URI without its own query → inherit base's query.
      - Relative variant URI with its own query → keep its query (don't merge).
    """
    joined = urljoin(base_url, uri)
    base = urlparse(base_url)
    if not base.query:
        return joined
    parsed_uri = urlparse(uri)
    if parsed_uri.scheme or parsed_uri.query:
        return joined
    j = urlparse(joined)
    return urlunparse(j._replace(query=base.query))


def classify(body: str) -> str:
    """Return the playlist classification for the given body."""
    if not body:
        return "unknown"
    cleaned = _strip_bom(body)
    head = cleaned.lstrip()
    if not head.startswith("#EXTM3U"):
        return "direct"
    if "#EXT-X-STREAM-INF:" in cleaned:
        return "hls_master"
    return "hls_media"


def _parse_attrs(attr_str: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_str):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out[key] = val.strip()
    return out


def _parse_resolution(s: str | None) -> tuple[int | None, int | None]:
    if not s or "x" not in s:
        return None, None
    w, _, h = s.partition("x")
    try:
        return int(w), int(h)
    except ValueError:
        return None, None


def _label(width: int | None, height: int | None, bandwidth: int) -> str:
    res = f"{width}×{height}" if width and height else "unknown"
    mbps = bandwidth / 1_000_000
    return f"{res} {mbps:.1f} Mbps"


def parse_master_playlist(body: str, base_url: str) -> list[dict]:
    """Return variants sorted by bandwidth descending. Kept for compatibility;
    new callers should use parse_master_streams() which also returns alternate
    audio/subtitle tracks."""
    return parse_master_streams(body, base_url)["variants"]


def _media_track_label(attrs: dict) -> str:
    name = attrs.get("NAME") or ""
    lang = attrs.get("LANGUAGE") or ""
    chans = attrs.get("CHANNELS") or ""
    bits = []
    if name:
        bits.append(name)
    elif lang:
        bits.append(lang)
    if chans:
        bits.append(f"{chans}ch")
    return " ".join(bits) or "track"


def parse_master_streams(body: str, base_url: str) -> dict:
    """Parse a master playlist into {variants, audio_tracks, subtitle_tracks}.

    - variants: #EXT-X-STREAM-INF entries, sorted by bandwidth desc.
    - audio_tracks: #EXT-X-MEDIA TYPE=AUDIO entries with a URI.
    - subtitle_tracks: #EXT-X-MEDIA TYPE=SUBTITLES entries with a URI.

    Tracks with no URI (typical for in-band audio) are skipped — there is
    nothing separate to download.
    """
    body = _strip_bom(body)
    lines = body.splitlines()

    variants: list[dict] = []
    audio_tracks: list[dict] = []
    subtitle_tracks: list[dict] = []

    for i, line in enumerate(lines):
        if line.startswith("#EXT-X-MEDIA:"):
            attrs = _parse_attrs(line[len("#EXT-X-MEDIA:") :])
            uri = attrs.get("URI")
            if not uri:
                continue
            kind = (attrs.get("TYPE") or "").upper()
            track = {
                "url": _resolve_variant_url(base_url, uri),
                "name": attrs.get("NAME") or "",
                "language": attrs.get("LANGUAGE") or "",
                "default": (attrs.get("DEFAULT") or "").upper() == "YES",
                "autoselect": (attrs.get("AUTOSELECT") or "").upper() == "YES",
                "channels": attrs.get("CHANNELS") or "",
                "codecs": attrs.get("CODECS") or "",
                "group_id": attrs.get("GROUP-ID") or "",
                "label": _media_track_label(attrs),
            }
            if kind == "AUDIO":
                audio_tracks.append(track)
            elif kind == "SUBTITLES":
                subtitle_tracks.append(track)
            continue

        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = _parse_attrs(line[len("#EXT-X-STREAM-INF:") :])
        uri = None
        for nxt in lines[i + 1 :]:
            stripped = nxt.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            uri = stripped
            break
        if not uri:
            continue
        w, h = _parse_resolution(attrs.get("RESOLUTION"))
        bw = int(attrs.get("BANDWIDTH", "0") or "0")
        frame_rate_raw = attrs.get("FRAME-RATE")
        try:
            frame_rate = float(frame_rate_raw) if frame_rate_raw else None
        except ValueError:
            frame_rate = None
        variants.append(
            {
                "url": _resolve_variant_url(base_url, uri),
                "width": w,
                "height": h,
                "bandwidth": bw,
                "codecs": attrs.get("CODECS", ""),
                "frame_rate": frame_rate,
                "label": _label(w, h, bw),
                "audio_group": attrs.get("AUDIO") or "",
                "subtitles_group": attrs.get("SUBTITLES") or "",
            }
        )
    variants.sort(key=lambda v: v["bandwidth"], reverse=True)
    return {
        "variants": variants,
        "audio_tracks": audio_tracks,
        "subtitle_tracks": subtitle_tracks,
    }


class UnsupportedSchemeError(ValueError):
    pass


@dataclass
class ProbeResult:
    type: str  # "hls_master" | "hls_media" | "direct" | "unknown"
    variants: list[dict] = field(default_factory=list)
    audio_tracks: list[dict] = field(default_factory=list)
    subtitle_tracks: list[dict] = field(default_factory=list)
    duration_seconds: float | None = None
    message: str | None = None


def fetch_url(url: str, *, max_bytes: int = 256 * 1024, timeout: float = 10.0) -> tuple[str, str]:
    """Fetch the URL and return (body, final_url).

    `final_url` is the URL after any redirects — the right base for resolving
    relative HLS variant URIs. Providers like Nebula 302-redirect the master
    URL to a signed CDN URL, and variant URIs in the body are relative to
    *that* redirected URL, not the user-supplied one.
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsupportedSchemeError(f"only http(s) allowed, got: {scheme}")
    req = Request(url, headers={"User-Agent": "ffmpeg-downloader-probe/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes)
        final_url = resp.geturl()
    return data.decode("utf-8", errors="replace"), final_url


def probe_url(url: str, *, max_bytes: int = 256 * 1024, timeout: float = 10.0) -> ProbeResult:
    """Fetch and classify a URL; return ProbeResult with variants if HLS master."""
    try:
        body, final_url = fetch_url(url, max_bytes=max_bytes, timeout=timeout)
    except UnsupportedSchemeError as e:
        return ProbeResult(type="unknown", message=str(e))
    except (URLError, TimeoutError, OSError, ValueError) as e:
        return ProbeResult(type="unknown", message=str(e))
    kind = classify(body)
    if kind == "hls_master":
        # Resolve all stream URIs against the post-redirect URL so providers
        # that move auth between query string and path (Nebula →
        # starlight.nebula.tv) work.
        streams = parse_master_streams(body, base_url=final_url)
        return ProbeResult(
            type=kind,
            variants=streams["variants"],
            audio_tracks=streams["audio_tracks"],
            subtitle_tracks=streams["subtitle_tracks"],
        )
    return ProbeResult(type=kind)
