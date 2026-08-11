# yt-dlp Extractor Downloads Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users paste a YouTube (or any yt-dlp-supported) video URL, pick a quality, and download into the existing folder/job/SSE flow via a dual pipeline (yt-dlp for extractors, ffmpeg for HLS/direct).

**Architecture:** New `ytdlp.py` module runs yt-dlp for metadata and downloads. Probe routes try extractor extraction before HLS/direct when the URL is not an obvious media file. `JobManager` gains a `backend` branch that runs yt-dlp with shared concurrency, cancel, and progress pubsub. UI reuses the resolution select for quality and auto-fills the title.

**Tech Stack:** Python 3.12, Flask, SQLite, yt-dlp CLI (subprocess), ffmpeg (merge/remux), vanilla JS, pytest, Docker.

**Spec:** `docs/superpowers/specs/2026-08-11-ytdlp-extractor-downloads-design.md`

## Global Constraints

- Public videos only — no cookies / auth in v1.
- Playlists and channels → probe `type: "unsupported"` with a clear message; no multi-job queue.
- Default quality: Best available (`bv*+ba/b`).
- Codec ignored for `backend: "ytdlp"`; extension remains user-controlled.
- Backend selected only when request sends `backend: "ytdlp"` (no inference from `format_selector` alone).
- No live network calls to YouTube in CI — use shell shims and JSON fixtures.
- Shared `MAX_CONCURRENT_JOBS` pool with ffmpeg jobs.
- Do not break existing HLS/ffmpeg tests or UI paths.

## File structure

| File | Responsibility |
|---|---|
| `ffmpeg_downloader/ytdlp.py` | Pure-ish helpers: URL media heuristics, format grouping, extract_info via subprocess, build argv, parse progress |
| `ffmpeg_downloader/config.py` | Add `ytdlp_bin` |
| `ffmpeg_downloader/db.py` | Columns `backend`, `format_selector`, `format_label` + migrate existing DBs |
| `ffmpeg_downloader/jobs.py` | `JobSpec` fields; submit/run branch for yt-dlp |
| `ffmpeg_downloader/routes.py` | Probe orchestration; download body fields |
| `ffmpeg_downloader/__init__.py` | Pass `ytdlp_bin` into `JobManager` |
| `ffmpeg_downloader/static/resolution-picker.js` | Handle `extractor` / `unsupported`; quality options; title auto-fill; codec disable |
| `ffmpeg_downloader/static/app.js` | Submit `backend` + `format_selector` / `format_label` |
| `ffmpeg_downloader/templates/index.html` | Quality label id (optional); codec hint element if needed |
| `Dockerfile` | Install yt-dlp |
| `README.md` | Feature + env var |
| `tests/fake_ytdlp.sh` | Shim for metadata / download / progress |
| `tests/fixtures/ytdlp-single-video.json` | Sanitized `yt-dlp -J` single video |
| `tests/fixtures/ytdlp-playlist.json` | Multi-entry playlist shape |
| `tests/test_ytdlp.py` | Unit tests for grouping / argv / progress / extract |
| `tests/test_config.py`, `test_db.py`, `test_jobs.py`, `test_api.py` | Extended coverage |
| `tests/conftest.py` | `fake_ytdlp_path` fixture |

---

### Task 1: Config — `YTDLP_BIN`

**Files:**
- Modify: `ffmpeg_downloader/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.ytdlp_bin: str` (default `"yt-dlp"`, from env `YTDLP_BIN`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_config.py`:

```python
def test_config_defaults(download_root: Path, data_dir: Path, monkeypatch):
    # existing assertions...
    assert cfg.ytdlp_bin == "yt-dlp"


def test_config_ytdlp_bin_override(download_root: Path, data_dir: Path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("YTDLP_BIN", "/opt/bin/yt-dlp")
    cfg = Config.from_env()
    assert cfg.ytdlp_bin == "/opt/bin/yt-dlp"
```

Update `test_config_defaults` to assert `cfg.ytdlp_bin == "yt-dlp"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL — `Config` has no `ytdlp_bin`

- [ ] **Step 3: Implement**

In `config.py` `Config` dataclass add:

```python
ytdlp_bin: str
```

In `from_env`:

```python
ytdlp_bin=e.get("YTDLP_BIN", "yt-dlp"),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/config.py tests/test_config.py
git commit -m "feat: add YTDLP_BIN config"
```

---

### Task 2: Database columns for extractor jobs

**Files:**
- Modify: `ffmpeg_downloader/db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Produces: `JOB_COLUMNS` includes `backend`, `format_selector`, `format_label`
- Produces: `Database.open` migrates older DBs via `ALTER TABLE` if columns missing
- Consumes: insert/update rows must supply the new keys (`backend` default `"ffmpeg"`)

- [ ] **Step 1: Write the failing tests**

Update `_make_job_row` in `tests/test_db.py` to include:

```python
backend="ffmpeg",
format_selector=None,
format_label=None,
```

Add:

```python
def test_insert_ytdlp_fields(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    row = _make_job_row(
        backend="ytdlp",
        format_selector="bv*+ba/b",
        format_label="Best available",
    )
    db.insert_job(row)
    fetched = db.get_job("j_test01")
    assert fetched["backend"] == "ytdlp"
    assert fetched["format_selector"] == "bv*+ba/b"
    assert fetched["format_label"] == "Best available"
    db.close()


def test_migrate_adds_columns_to_legacy_db(data_dir: Path):
    """Simulate a pre-ytdlp schema and ensure open() adds columns."""
    import sqlite3

    path = data_dir / "legacy.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE jobs (
          id TEXT PRIMARY KEY,
          url TEXT NOT NULL,
          selected_variant_url TEXT,
          selected_variant_label TEXT,
          filename TEXT NOT NULL,
          output_path TEXT NOT NULL,
          extension TEXT NOT NULL,
          codec TEXT NOT NULL,
          command TEXT NOT NULL,
          status TEXT NOT NULL,
          progress REAL,
          duration_seconds REAL,
          current_time_seconds REAL,
          speed TEXT,
          message TEXT,
          created_at INTEGER NOT NULL,
          started_at INTEGER,
          finished_at INTEGER
        );
        """
    )
    conn.close()
    db = Database.open(path)
    row = _make_job_row(id="j_legacy")
    db.insert_job(row)
    fetched = db.get_job("j_legacy")
    assert fetched["backend"] == "ffmpeg"
    assert fetched["format_selector"] is None
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL on missing columns / insert keys

- [ ] **Step 3: Implement schema + migration**

Update `SCHEMA` / `JOB_COLUMNS` in `db.py`:

```python
# In CREATE TABLE add after codec (or at end before created_at is fine):
  backend                TEXT NOT NULL DEFAULT 'ffmpeg',
  format_selector        TEXT,
  format_label           TEXT,
```

And in `JOB_COLUMNS` tuple after `"codec"`:

```python
"backend",
"format_selector",
"format_label",
```

After `conn.executescript(SCHEMA)` in `open`, migrate:

```python
@classmethod
def open(cls, path: Path) -> Database:
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    db = cls(conn)
    db._migrate()
    return db

def _migrate(self) -> None:
    cur = self._conn.execute("PRAGMA table_info(jobs)")
    existing = {row[1] for row in cur.fetchall()}
    alters = []
    if "backend" not in existing:
        alters.append("ALTER TABLE jobs ADD COLUMN backend TEXT NOT NULL DEFAULT 'ffmpeg'")
    if "format_selector" not in existing:
        alters.append("ALTER TABLE jobs ADD COLUMN format_selector TEXT")
    if "format_label" not in existing:
        alters.append("ALTER TABLE jobs ADD COLUMN format_label TEXT")
    for sql in alters:
        self._conn.execute(sql)
```

- [ ] **Step 4: Fix all call sites that build job rows**

Any code that inserts full rows must include the new keys. At minimum `jobs.py` `submit` row dict and `tests/test_db.py` `_make_job_row`. Grep for `insert_job` and `"codec":` in tests and fix so suite still constructs valid rows.

For now in Task 2, if `jobs.py` insert breaks the suite, add default keys:

```python
"backend": "ffmpeg",
"format_selector": None,
"format_label": None,
```

in the existing `submit` row (full ytdlp branch comes in Task 5).

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_db.py tests/test_jobs.py tests/test_api.py -q`
Expected: PASS (or only failures unrelated to schema after defaults)

- [ ] **Step 6: Commit**

```bash
git add ffmpeg_downloader/db.py ffmpeg_downloader/jobs.py tests/test_db.py
git commit -m "feat: persist ytdlp backend fields on jobs"
```

---

### Task 3: `ytdlp.py` — pure helpers (grouping, progress, argv, media heuristic)

**Files:**
- Create: `ffmpeg_downloader/ytdlp.py`
- Create: `tests/test_ytdlp.py`
- Create: `tests/fixtures/ytdlp-single-video.json`

**Interfaces:**
- Produces:
  - `looks_like_direct_media(url: str) -> bool`
  - `group_formats(info: dict) -> list[dict]` — each dict: `id`, `label`, `format_selector`, `height`, `ext`, `is_audio_only`
  - `build_download_argv(*, ytdlp_bin: str, url: str, format_selector: str, output_path: str, extension: str) -> list[str]`
  - `parse_progress_line(line: str) -> dict | None` — e.g. `{"percent": 12.5, "speed": "1.2MiB/s"}` or `None`
  - `DEFAULT_FORMAT_SELECTOR = "bv*+ba/b"`

- [ ] **Step 1: Create a minimal fixture**

`tests/fixtures/ytdlp-single-video.json` — enough shape for grouping (not a full dump):

```json
{
  "_type": "video",
  "id": "dQw4w9WgXcQ",
  "title": "Sample Video Title",
  "extractor": "youtube",
  "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
  "duration": 212,
  "formats": [
    {"format_id": "18", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a", "tbr": 500},
    {"format_id": "137", "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none", "tbr": 2500},
    {"format_id": "136", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "none", "tbr": 1500},
    {"format_id": "140", "ext": "m4a", "height": null, "vcodec": "none", "acodec": "mp4a", "tbr": 128},
    {"format_id": "22", "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a", "tbr": 800}
  ]
}
```

- [ ] **Step 2: Write failing unit tests**

```python
# tests/test_ytdlp.py
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
    # merge/remux toward requested container
    assert "--merge-output-format" in argv
    assert "mp4" in argv


def test_parse_progress_line_percent():
    # Support a simple custom template line the shim will emit, e.g.:
    # PROGRESS percent=12.5 speed=1.2MiB/s
    parsed = y.parse_progress_line("PROGRESS percent=12.5 speed=1.2MiB/s")
    assert parsed == {"percent": 12.5, "speed": "1.2MiB/s"}
    assert y.parse_progress_line("not progress") is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_ytdlp.py -v`
Expected: FAIL — module missing

- [ ] **Step 4: Implement `ytdlp.py` pure helpers**

```python
# ffmpeg_downloader/ytdlp.py (core pure pieces)
from __future__ import annotations

import re
from urllib.parse import urlparse

DEFAULT_FORMAT_SELECTOR = "bv*+ba/b"
_HEIGHT_BUCKETS = (2160, 1440, 1080, 720, 480, 360)
_MEDIA_EXTS = {
    ".m3u8", ".mp4", ".webm", ".mkv", ".mp3", ".m4a", ".ts",
    ".mov", ".avi", ".flac", ".ogg", ".opus", ".wav",
}


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
) -> list[str]:
    return [
        ytdlp_bin,
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


_PROGRESS_RE = re.compile(
    r"^PROGRESS\s+percent=(?P<percent>\S+)\s+speed=(?P<speed>\S+)\s*$"
)
_DOWNLOAD_PCT_RE = re.compile(r"\[download\]\s+(?P<percent>\d+(?:\.\d+)?)%")


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
```

Note: yt-dlp's `progress._percent_str` may include a `%` sign — the parser strips it. The fake shim emits clean `PROGRESS percent=12.5 speed=1.2MiB/s` lines. The `[download] N%` fallback covers default yt-dlp output if the template is ignored.

- [ ] **Step 5: Run unit tests**

Run: `pytest tests/test_ytdlp.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ffmpeg_downloader/ytdlp.py tests/test_ytdlp.py tests/fixtures/ytdlp-single-video.json
git commit -m "feat: add ytdlp format grouping and argv helpers"
```

---

### Task 4: `ytdlp.py` — metadata extraction via subprocess

**Files:**
- Modify: `ffmpeg_downloader/ytdlp.py`
- Create: `tests/fake_ytdlp.sh`
- Create: `tests/fixtures/ytdlp-playlist.json`
- Modify: `tests/test_ytdlp.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Produces:
  - `extract_info(url: str, *, ytdlp_bin: str, timeout: float = 60.0) -> dict`
    - On success returns a result dict:
      - `{"ok": True, "kind": "video", "info": <yt-dlp json>}`
      - `{"ok": True, "kind": "playlist", "info": <json>}` when multi-entry / playlist
      - `{"ok": False, "error": "<message>"}` on failure / no extractor
  - `probe_extractor(url: str, *, ytdlp_bin: str) -> dict` — UI-shaped probe payload:
    - success video → `{type, title, extractor, webpage_url, duration_seconds, formats, variants: []}`
    - playlist → `{type: "unsupported", message: "...", variants: [], formats: []}`
    - failure with recognized site error → `{type: "unsupported", message: ..., variants: [], formats: []}`
    - no extractor / generic fail → `{type: "none"}` so routes fall through to HLS probe

**Classification rules for `extract_info`:**
- If JSON has `_type == "playlist"` or a non-empty `entries` list with more than one entry (or `_type` playlist) → `kind: "playlist"`.
- If single video (`_type` in `(None, "video")` or single entry) → `kind: "video"`.
- Non-zero exit → `ok: False` with stderr message (truncated to ~500 chars).

- [ ] **Step 1: Write `tests/fake_ytdlp.sh`**

```bash
#!/usr/bin/env bash
# Test shim for yt-dlp.
# Env:
#   FAKE_YTDLP_MODE=dump|download   (default: if -J/--dump-single-json present → dump)
#   FAKE_YTDLP_JSON_FILE=path       required for dump mode
#   FAKE_YTDLP_EXIT=0
#   FAKE_YTDLP_STDERR=""
#   FAKE_YTDLP_TICKS=3
#   FAKE_YTDLP_SLEEP=0.05
set -u

exit_code="${FAKE_YTDLP_EXIT:-0}"
stderr_text="${FAKE_YTDLP_STDERR:-}"

is_dump=0
output_path=""
prev=""
for arg in "$@"; do
  if [ "$arg" = "-J" ] || [ "$arg" = "--dump-single-json" ] || [ "$arg" = "--dump-json" ]; then
    is_dump=1
  fi
  if [ "$prev" = "-o" ]; then
    output_path="$arg"
  fi
  prev="$arg"
done

if [ "$is_dump" = "1" ]; then
  if [ "$exit_code" != "0" ]; then
    printf '%s' "$stderr_text" >&2
    exit "$exit_code"
  fi
  if [ -z "${FAKE_YTDLP_JSON_FILE:-}" ] || [ ! -f "$FAKE_YTDLP_JSON_FILE" ]; then
    printf 'FAKE_YTDLP_JSON_FILE missing\n' >&2
    exit 2
  fi
  cat "$FAKE_YTDLP_JSON_FILE"
  exit 0
fi

# download mode
ticks="${FAKE_YTDLP_TICKS:-3}"
sleep_s="${FAKE_YTDLP_SLEEP:-0.05}"
for i in $(seq 1 "$ticks"); do
  pct=$(awk "BEGIN { printf \"%.1f\", ($i / $ticks) * 100 }")
  printf 'PROGRESS percent=%s speed=1.0MiB/s\n' "$pct"
  sleep "$sleep_s"
done

if [ "$exit_code" = "0" ]; then
  if [ -n "$output_path" ]; then
    : > "$output_path"
  fi
else
  printf '%s' "$stderr_text" >&2
fi
exit "$exit_code"
```

- [ ] **Step 2: Playlist fixture**

`tests/fixtures/ytdlp-playlist.json`:

```json
{
  "_type": "playlist",
  "id": "PLtest",
  "title": "A Playlist",
  "extractor": "youtube",
  "webpage_url": "https://www.youtube.com/playlist?list=PLtest",
  "entries": [
    {"id": "aaa", "title": "One"},
    {"id": "bbb", "title": "Two"}
  ]
}
```

- [ ] **Step 3: conftest fixture**

```python
@pytest.fixture
def fake_ytdlp_path() -> Path:
    return Path(__file__).parent / "fake_ytdlp.sh"


@pytest.fixture(autouse=True)
def _ensure_shims_executable(fake_ffmpeg_path: Path, fake_ffprobe_path: Path, fake_ytdlp_path: Path) -> None:
    for p in (fake_ffmpeg_path, fake_ffprobe_path, fake_ytdlp_path):
        if p.exists():
            st = p.stat()
            p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
```

- [ ] **Step 4: Failing tests for extract/probe**

```python
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
    result = y.extract_info("https://www.youtube.com/watch?v=x", ytdlp_bin=str(fake_ytdlp_path))
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
```

- [ ] **Step 5: Implement extract_info + probe_extractor**

```python
import json
import subprocess
from typing import Any


def extract_info(url: str, *, ytdlp_bin: str, timeout: float = 60.0) -> dict[str, Any]:
    argv = [
        ytdlp_bin,
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
    if info.get("_type") == "playlist":
        kind = "playlist"
    elif isinstance(info.get("entries"), list) and len(info["entries"]) > 1:
        kind = "playlist"
    return {"ok": True, "kind": kind, "info": info}


def probe_extractor(url: str, *, ytdlp_bin: str) -> dict[str, Any]:
    result = extract_info(url, ytdlp_bin=ytdlp_bin)
    if not result["ok"]:
        err = result["error"]
        # If binary missing or no extractor, signal fall-through with type none
        low = err.lower()
        if "not found" in low or "unsupported url" in low or "no suitable" in low:
            return {"type": "none", "message": err}
        return {
            "type": "unsupported",
            "message": err,
            "variants": [],
            "formats": [],
        }
    if result["kind"] == "playlist":
        return {
            "type": "unsupported",
            "message": "Playlists and channels are not supported. Paste a single video URL.",
            "variants": [],
            "formats": [],
        }
    info = result["info"]
    return {
        "type": "extractor",
        "title": info.get("title") or "",
        "extractor": info.get("extractor") or info.get("ie_key") or "",
        "webpage_url": info.get("webpage_url") or url,
        "duration_seconds": float(info["duration"]) if info.get("duration") is not None else None,
        "formats": group_formats(info),
        "variants": [],
    }
```

Tune "fall through" vs "unsupported" using the tests; prefer: binary missing → `type: "none"`; private video stderr → `unsupported`.

- [ ] **Step 6: Run tests**

Run: `pytest tests/test_ytdlp.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add ffmpeg_downloader/ytdlp.py tests/fake_ytdlp.sh tests/fixtures/ytdlp-playlist.json tests/test_ytdlp.py tests/conftest.py
git commit -m "feat: extract metadata via yt-dlp subprocess"
```

---

### Task 5: JobManager — yt-dlp download backend

**Files:**
- Modify: `ffmpeg_downloader/jobs.py`
- Modify: `ffmpeg_downloader/__init__.py`
- Modify: `tests/test_jobs.py`

**Interfaces:**
- Consumes: `ytdlp.build_download_argv`, `ytdlp.parse_progress_line`, `ytdlp.DEFAULT_FORMAT_SELECTOR`
- Produces:
  - `JobSpec.backend: str = "ffmpeg"`
  - `JobSpec.format_selector: str | None = None`
  - `JobSpec.format_label: str | None = None`
  - `JobManager(..., ytdlp_bin: str)`
  - `submit` stores backend fields; for `backend == "ytdlp"` builds yt-dlp argv and runs with percent progress

- [ ] **Step 1: Write failing job tests**

```python
@pytest.fixture
def jm(db, fs, fake_ffmpeg_path, fake_ffprobe_path, fake_ytdlp_path):
    manager = JobManager(
        db=db,
        fs=fs,
        ffmpeg_bin=str(fake_ffmpeg_path),
        ffprobe_bin=str(fake_ffprobe_path),
        ytdlp_bin=str(fake_ytdlp_path),
        max_concurrent_jobs=2,
    )
    try:
        yield manager
    finally:
        manager.shutdown(wait=True)


def test_submit_ytdlp_persists_backend_fields(jm, db):
    jm._submit_to_executor = lambda *_a, **_k: None
    job = jm.submit(
        JobSpec(
            url="https://www.youtube.com/watch?v=abc",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="yt clip",
            extension="mp4",
            codec="copy",
            output_folder="",
            backend="ytdlp",
            format_selector="bv*+ba/b",
            format_label="Best available",
        )
    )
    assert job["backend"] == "ytdlp"
    assert job["format_selector"] == "bv*+ba/b"
    assert "yt-dlp" in job["command"] or "fake_ytdlp" in job["command"]
    stored = db.get_job(job["id"])
    assert stored["backend"] == "ytdlp"


def test_run_ytdlp_job_completes(jm, db, download_root, monkeypatch):
    monkeypatch.setenv("FAKE_YTDLP_TICKS", "2")
    monkeypatch.setenv("FAKE_YTDLP_SLEEP", "0")
    monkeypatch.setenv("FAKE_YTDLP_EXIT", "0")
    job = jm.submit(
        JobSpec(
            url="https://www.youtube.com/watch?v=abc",
            selected_variant_url=None,
            selected_variant_label=None,
            filename="yt clip",
            extension="mp4",
            codec="copy",
            output_folder="",
            backend="ytdlp",
            format_selector="bv*+ba/b",
            format_label="Best available",
        )
    )
    # wait for completion
    deadline = time.time() + 8
    while time.time() < deadline:
        row = db.get_job(job["id"])
        if row["status"] == "completed":
            break
        time.sleep(0.05)
    row = db.get_job(job["id"])
    assert row["status"] == "completed"
    assert (download_root / row["output_path"]).exists()
    assert row["progress"] == 100.0
```

Also update every existing `JobManager(...)` construction in tests to pass `ytdlp_bin=str(fake_ytdlp_path)` (or a dummy path for ffmpeg-only tests).

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_jobs.py::test_submit_ytdlp_persists_backend_fields -v`
Expected: FAIL — unexpected kwargs / missing fields

- [ ] **Step 3: Implement JobSpec + submit branch**

```python
@dataclass
class JobSpec:
    url: str
    selected_variant_url: str | None
    selected_variant_label: str | None
    filename: str
    extension: str
    codec: str
    output_folder: str
    audio_urls: list[str] = field(default_factory=list)
    subtitle_urls: list[str] = field(default_factory=list)
    backend: str = "ffmpeg"
    format_selector: str | None = None
    format_label: str | None = None
```

`JobManager.__init__` add `ytdlp_bin: str` and `self._ytdlp_bin = ytdlp_bin`.

In `submit`, after computing `out_abs` / `filename`:

```python
backend = spec.backend if spec.backend in ("ffmpeg", "ytdlp") else "ffmpeg"
if backend == "ytdlp":
    from . import ytdlp as _ytdlp
    selector = spec.format_selector or _ytdlp.DEFAULT_FORMAT_SELECTOR
    argv = _ytdlp.build_download_argv(
        ytdlp_bin=self._ytdlp_bin,
        url=spec.url,
        format_selector=selector,
        output_path=out_abs,
        extension=spec.extension,
    )
else:
    # existing ffmpeg argv logic
    ...
```

Row dict:

```python
"backend": backend,
"format_selector": spec.format_selector if backend == "ytdlp" else None,
"format_label": spec.format_label if backend == "ytdlp" else None,
```

- [ ] **Step 4: Implement run path for yt-dlp progress**

In `_run_job_impl`, branch on job row backend:

```python
backend = job_row.get("backend") or "ffmpeg"
if backend == "ytdlp":
    # skip ffprobe duration optional; set duration from None
    duration = job_row.get("duration_seconds")  # may already be null
else:
    duration = _probe_duration(...)
```

When reading stdout for ytdlp:

```python
from .ytdlp import parse_progress_line as parse_ytdlp_progress
...
if backend == "ytdlp":
    for raw_line in proc.stdout:
        parsed = parse_ytdlp_progress(raw_line.strip())
        if not parsed:
            continue
        pct = min(100.0, max(0.0, float(parsed["percent"])))
        speed = parsed.get("speed")
        with self._db_lock:
            self._db.update_job(job_id, progress=pct, speed=speed)
        self._publish_progress(job_id, pct, None, speed)
else:
    # existing ffmpeg progress loop
```

On `FileNotFoundError`, message should say `yt-dlp not found` when backend is ytdlp.

Wire `create_app`:

```python
jobs = JobManager(
    ...
    ytdlp_bin=config.ytdlp_bin,
)
```

- [ ] **Step 5: Run job tests**

Run: `pytest tests/test_jobs.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ffmpeg_downloader/jobs.py ffmpeg_downloader/__init__.py tests/test_jobs.py
git commit -m "feat: run extractor downloads via yt-dlp backend"
```

---

### Task 6: Routes — probe orchestration + download fields

**Files:**
- Modify: `ffmpeg_downloader/routes.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `ytdlp.looks_like_direct_media`, `ytdlp.probe_extractor`
- Produces: probe JSON with `type: extractor | unsupported | ...` plus existing fields; download accepts `backend`, `format_selector`, `format_label`

- [ ] **Step 1: Write API tests**

Extend `app` fixture overrides with `"YTDLP_BIN": str(fake_ytdlp_path)`.

```python
def test_probe_extractor_youtube(client, fake_ytdlp_path, monkeypatch):
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


def test_probe_m3u8_skips_ytdlp(client, probe_stub, monkeypatch):
    # Ensure HLS still works; ytdlp should not be required
    fixtures = Path(__file__).parent / "fixtures"
    _ProbeStub.body_bytes = (fixtures / "master-simple.m3u8").read_bytes()
    host, port = probe_stub.server_address
    url = f"http://{host}:{port}/master.m3u8"
    r = client.post("/api/probe", json={"url": url})
    assert r.get_json()["type"] == "hls_master"


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
```

- [ ] **Step 2: Run to verify fail**

Run: `pytest tests/test_api.py::test_probe_extractor_youtube -v`
Expected: FAIL — type not extractor

- [ ] **Step 3: Implement probe orchestration**

```python
from . import ytdlp as _ytdlp

@app.post("/api/probe")
def probe():
    ...
    if scheme not in ("http", "https"):
        return jsonify({"error": "only http(s) URLs are allowed"}), 400

    cfg = current_app.extensions["config"]
    if not _ytdlp.looks_like_direct_media(url):
        ext = _ytdlp.probe_extractor(url, ytdlp_bin=cfg.ytdlp_bin)
        if ext.get("type") == "extractor":
            return jsonify(ext)
        if ext.get("type") == "unsupported":
            return jsonify(ext)
        # type == "none" → fall through

    result = _probe.probe_url(url)
    return jsonify({...existing fields...})
```

Ensure extractor responses include keys the UI may read (`message` optional).

- [ ] **Step 4: Implement download fields**

```python
backend = body.get("backend") or "ffmpeg"
if backend not in ("ffmpeg", "ytdlp"):
    return jsonify({"error": "backend must be ffmpeg or ytdlp"}), 400

spec = JobSpec(
    ...
    backend=backend,
    format_selector=body.get("format_selector") or None,
    format_label=body.get("format_label") or None,
)
```

For `backend == "ytdlp"`, still require http(s) on `url` only (skip variant/audio URL checks if empty). Codec may still be present in body; ignore it.

- [ ] **Step 5: Run API + full suite**

Run: `pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ffmpeg_downloader/routes.py tests/test_api.py
git commit -m "feat: probe and submit yt-dlp extractor downloads"
```

---

### Task 7: UI — quality picker, title auto-fill, codec disable, submit payload

**Files:**
- Modify: `ffmpeg_downloader/static/resolution-picker.js`
- Modify: `ffmpeg_downloader/static/app.js`
- Modify: `ffmpeg_downloader/templates/index.html` (label text / data attributes / codec hint)

**Interfaces:**
- Produces: module-level or exported state for current probe mode:
  - `getProbeMode()` → `"ffmpeg" | "ytdlp" | null`
  - `getSelectedFormat()` → `{ format_selector, format_label } | null`
- On extractor probe: populate quality select from `formats`; auto-fill filename if empty; disable `#codecSelect`; set hint
- On unsupported: show message; set `data-submit-blocked` or disable submit button
- Submit body includes `backend`, `format_selector`, `format_label` when mode is ytdlp

- [ ] **Step 1: HTML tweaks**

In `index.html`:
- Change resolution label to be updatable: keep `id="resolutionLabel"` text default “Video resolution”.
- Add optional `<div id="codecHint" class="hint" hidden></div>` under codec select.

- [ ] **Step 2: resolution-picker.js extractor handling**

Extend probe response handling:

```javascript
} else if (body.type === "extractor" && body.formats && body.formats.length) {
  populateExtractorFormats(body.formats);
  urlHint.textContent = body.extractor
    ? `Detected ${body.extractor} — choose quality`
    : "Site video — choose quality";
  const filenameInput = document.getElementById("filenameInput");
  if (filenameInput && body.title && !filenameInput.value.trim()) {
    filenameInput.value = body.title;
  }
  setCodecEnabled(false);
  setSubmitBlocked(false);
  window.__ffdProbeMode = "ytdlp";
} else if (body.type === "unsupported") {
  hideAllPickers();
  setCodecEnabled(true);
  urlHint.textContent = body.message || "Unsupported URL";
  setSubmitBlocked(true);
  window.__ffdProbeMode = null;
} else if ...
```

`populateExtractorFormats(formats)`:
- Clear select; for each format set `option.value = format.format_selector`, `option.textContent = format.label`, `option.dataset.label = format.label`.
- Select first (best).
- Show resolution group; set label text to “Quality”.

`setCodecEnabled(on)`: toggle `disabled` on `#codecSelect` and codec hint visibility (“Codec does not apply to site downloads”).

`setSubmitBlocked(blocked)`: disable the form submit button when true.

On `hideAllPickers` / non-extractor paths: `window.__ffdProbeMode = "ffmpeg"` or null; re-enable codec; unblock submit; reset label to “Video resolution”.

Export:

```javascript
export function getDownloadBackend() {
  return window.__ffdProbeMode === "ytdlp" ? "ytdlp" : "ffmpeg";
}

export function getSelectedFormat() {
  if (window.__ffdProbeMode !== "ytdlp") return null;
  const opt = resolutionSelect.selectedOptions[0];
  if (!opt) return { format_selector: "bv*+ba/b", format_label: "Best available" };
  return {
    format_selector: opt.value || "bv*+ba/b",
    format_label: opt.dataset.label || opt.textContent,
  };
}
```

- [ ] **Step 3: app.js submit**

```javascript
import { getDownloadBackend, getSelectedFormat } from "./resolution-picker.js";

// in submit handler:
const backend = getDownloadBackend();
const body = {
  url: urlInput.value.trim(),
  filename: $("filenameInput").value.trim(),
  extension: $("extensionSelect").value,
  codec: $("codecSelect").value,
  output_folder: $("outputFolder").value.trim(),
};
if (backend === "ytdlp") {
  const fmt = getSelectedFormat() || {};
  body.backend = "ytdlp";
  body.format_selector = fmt.format_selector;
  body.format_label = fmt.format_label;
} else {
  body.backend = "ffmpeg";
  body.selected_variant_url = videoUrl;
  body.selected_variant_label = videoLabel;
  body.audio_urls = audioUrls;
  body.subtitle_urls = subtitleUrls;
}
```

- [ ] **Step 4: Manual UI check (dev server)**

```bash
# with fake or real yt-dlp
DOWNLOAD_ROOT=/tmp/ffd-root DATA_DIR=/tmp/ffd-data \
  flask --app ffmpeg_downloader run --debug --port 5050
```

Paste a YouTube URL if network + yt-dlp available; otherwise skip to automated tests only and verify JS syntax loads on `/`.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/static/resolution-picker.js ffmpeg_downloader/static/app.js ffmpeg_downloader/templates/index.html
git commit -m "feat: UI quality picker for extractor downloads"
```

---

### Task 8: Docker + README

**Files:**
- Modify: `Dockerfile`
- Modify: `README.md`

- [ ] **Step 1: Install yt-dlp in the image**

After pip install of the app (or in the apt layer), install yt-dlp via pip for fresher extractors:

```dockerfile
RUN pip install --no-cache-dir . yt-dlp
```

Or separate:

```dockerfile
RUN pip install --no-cache-dir yt-dlp
```

Ensure `ffmpeg` remains installed (yt-dlp uses it for merges).

- [ ] **Step 2: README**

Features bullet:

```markdown
- Paste a YouTube / site URL (yt-dlp); pick quality; download like any other job.
```

Env table row:

```markdown
| `YTDLP_BIN` | `yt-dlp` | Path to the yt-dlp binary for site downloads. |
```

Note under architecture or features:

```markdown
Site downloads use [yt-dlp](https://github.com/yt-dlp/yt-dlp). Extractors break when sites change — rebuild/pull a fresh image periodically. Public videos only (no cookies) in this version. Playlists/channels are rejected; paste a single video URL.
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile README.md
git commit -m "docs: ship yt-dlp in Docker image and document YTDLP_BIN"
```

---

### Task 9: Full verification + regression

**Files:** none expected unless fixes

- [ ] **Step 1: Run full test suite**

Run: `pytest -q`
Expected: PASS, all green

- [ ] **Step 2: Run linter**

Run: `ruff check ffmpeg_downloader tests && ruff format --check ffmpeg_downloader tests`
Expected: clean (format if needed)

- [ ] **Step 3: Fix any failures**

If real yt-dlp progress template differs, expand `parse_progress_line` to also match:

```python
m2 = re.search(r"\[download\]\s+(\d+(?:\.\d+)?)%", line)
```

Add a unit test for that fallback.

- [ ] **Step 4: Final commit if fixes landed**

```bash
git add -A
git commit -m "fix: harden ytdlp progress parsing and lint"
```

---

## Self-review (plan vs spec)

| Spec requirement | Task |
|---|---|
| Dual pipeline ffmpeg / ytdlp | 5, 6 |
| Any yt-dlp site | 4, 6 (probe_extractor) |
| Quality grouping + Best default | 3, 7 |
| Auto-fill title | 7 |
| Public only / no cookies | Global + 4 (no cookie flags) |
| Reject playlists | 4, 6 |
| Extension kept, codec ignored | 5, 7 |
| `backend` only when `"ytdlp"` | 6 |
| DB columns | 2 |
| `YTDLP_BIN` | 1, 8 |
| Progress + SSE reuse | 5 |
| Cancel process | existing cancel works on `_procs` |
| Docker yt-dlp | 8 |
| No live CI network | 3–6 shims/fixtures |
| README | 8 |

No TBD placeholders remain in tasks. Types aligned: `backend` string `"ffmpeg"|"ytdlp"`, `format_selector` / `format_label` optional strings, probe `type` values `extractor` | `unsupported` | `none` (internal) | existing HLS types.
