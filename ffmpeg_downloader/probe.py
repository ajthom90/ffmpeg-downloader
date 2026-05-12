from __future__ import annotations

import re
from urllib.parse import urljoin

_ATTR_RE = re.compile(r'([A-Z0-9-]+)\s*=\s*(?:"([^"]*)"|([^,]*))(?:,\s*|$)')


def _strip_bom(s: str) -> str:
    return s.lstrip("﻿")


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
    """Return variants sorted by bandwidth descending."""
    body = _strip_bom(body)
    lines = body.splitlines()
    variants: list[dict] = []
    for i, line in enumerate(lines):
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
                "url": urljoin(base_url, uri),
                "width": w,
                "height": h,
                "bandwidth": bw,
                "codecs": attrs.get("CODECS", ""),
                "frame_rate": frame_rate,
                "label": _label(w, h, bw),
            }
        )
    variants.sort(key=lambda v: v["bandwidth"], reverse=True)
    return variants
