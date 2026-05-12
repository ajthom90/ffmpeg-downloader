# ffmpeg-downloader — Design

A small self-hosted web app that wraps `ffmpeg` to download media (especially HLS / `.m3u8` streams) into a configured media library. Rebuild of an existing personal app whose source was lost; this rebuild adds a better folder picker, SSE-based progress, and SQLite-backed history.

- **Repo**: <https://github.com/ajthom90/ffmpeg-downloader>
- **Existing deployment**: <https://ffmpeg.home.njathome.net>
- **Target deployment**: same host, Docker, replacing the existing container
- **License**: MIT (public open-source)

## 1. Goals & non-goals

### Goals
- One-page web UI to submit a URL → download to a chosen subfolder of a configured media root.
- Default codec `copy` (no transcode) — the path used 99% of the time.
- Optional transcoding presets (h264, h265, vp9, aac, mp3).
- A folder picker that supports five input styles:
  - Browsing a tree (today's behavior).
  - Type-to-filter the currently visible folder.
  - Path autocomplete in the field itself.
  - Paste-a-path with live validation.
  - Recursive search across the whole tree.
- **URL probing**: when an HLS master playlist (`.m3u8`) is entered, detect the available variants and let the user pick a resolution before submitting. Default is "Auto (highest)" — match today's implicit behavior.
- Real-time progress via SSE.
- Job history that survives container restarts.
- Multi-arch (`linux/amd64`, `linux/arm64`) Docker image published to GHCR via GitHub Actions.

### Non-goals
- Authentication. The app trusts its reverse proxy and the LAN it sits behind.
- Multi-user features (separate job lists, quotas, etc.).
- Resuming jobs across container restarts. Interrupted jobs are marked `failed`.
- General-purpose video tooling beyond an `ffmpeg` wrapper (no yt-dlp integration, no transcode farm, no media-server hand-off).
- Mobile-specific UI work. The page should be usable on mobile but it isn't the target.

## 2. Architecture

Single Flask application packaged in one Docker image. One gunicorn worker (`gthread`, 16 threads) — the in-process `JobManager` owns a `ThreadPoolExecutor` (`max_workers = MAX_CONCURRENT_JOBS`, default 2) that runs `ffmpeg` subprocesses. SSE streams pull progress from a per-job in-memory pub/sub fanout. SQLite (WAL mode) under `/data` persists the job table.

```
Browser ──HTTP──> Flask routes ──> JobManager ──> ThreadPoolExecutor
                       │                │
                       │                ├──> subprocess.Popen(ffmpeg)
                       │                ├──> progress parser ──┐
                       │                └──> SQLite (jobs)     │
                       │                                       │
                       └──SSE─── Pubsub <──────publish─────────┘
```

### Why one worker, many threads
The JobManager holds in-memory state — the executor, the per-job pub/sub queues, the path-search cache. Multiple gunicorn workers would each spin up their own copy of this state, fragmenting SSE subscribers and the running-jobs view. One worker with a thread pool is the right shape for a single-tenant app of this size.

### Filesystem layout (inside container)
```
/downloads          ← bind-mount of the host media root (configurable)
/data/jobs.db       ← SQLite, lives on a small data volume
/app/...            ← the Python code
```

### Module boundaries
| Module | Responsibility | Knows about |
|---|---|---|
| `config.py` | Read env vars into a typed `Config` object | Env only |
| `filesystem.py` | browse / mkdir / validate / autocomplete / recursive search; path-traversal safety | Config + stdlib |
| `ffmpeg_command.py` | Build the `ffmpeg` argv from a job spec | None (pure functions) |
| `probe.py` | Fetch a URL, detect if it's an HLS master playlist, enumerate variants | stdlib (`urllib`) |
| `db.py` | SQLite CRUD on the `jobs` table | sqlite3 + stdlib |
| `jobs.py` | JobManager: queue, executor, subprocess lifecycle, progress parser, pubsub | db, ffmpeg_command, filesystem |
| `routes.py` | HTTP/SSE endpoints, request validation, response shaping | jobs, filesystem, db, probe |
| `__init__.py` | `create_app()` Flask factory wiring config + JobManager into `app.extensions` | All of above |

Each module is independently unit-testable. `ffmpeg_command`, `probe`, and `filesystem` are pure / near-pure (probe takes a URL and bytes-fetcher func, which is mocked in tests). `jobs` is tested with a `fake_ffmpeg.sh` shim. `routes` is tested with Flask's test client.

## 3. Configuration

All via env vars, surfaced as a single `Config` dataclass.

| Env var | Default | Purpose |
|---|---|---|
| `DOWNLOAD_ROOT` | `/downloads` | The media root mounted into the container. Everything the user can browse / write to lives under this directory. Resolved (symlinks followed) once at startup. |
| `DATA_DIR` | `/data` | Where `jobs.db` lives. |
| `PORT` | `8000` | gunicorn bind port. |
| `MAX_CONCURRENT_JOBS` | `2` | ThreadPoolExecutor `max_workers`. |
| `JOB_RETENTION_DAYS` | `30` | Completed / failed / cancelled jobs older than this are deleted on startup. |
| `SEARCH_CACHE_TTL_SECONDS` | `60` | TTL on the recursive-search folder cache. |
| `SEARCH_RESULT_LIMIT` | `50` | Max recursive-search results returned. |

Startup invariants (all fatal if violated):
- `DOWNLOAD_ROOT` exists, is a directory, and is writable.
- `DATA_DIR` exists (created on first run) and is writable.

## 4. HTTP / SSE API

All responses JSON unless noted. Errors: `{ "error": "<message>" }` with appropriate 4xx/5xx status.

### Page
- `GET /` — the single HTML page (templated, includes the version SHA in a `<meta>` tag).

### Filesystem
- `GET /api/browse?path=<rel>` — immediate children of `path`.
  ```json
  { "current_path": "Movies", "parent": "", "items": [ {"name":"...", "path":"Movies/...", "is_dir":true}, ... ] }
  ```
  Sorted: directories first, then files; case-insensitive name within each group. `.DS_Store` and other dotfiles are returned (matches existing app) so the UI can hide them if desired — keep behavior identical to existing.
- `POST /api/mkdir` — body `{ "path": "<rel>", "name": "<segment>" }`. Creates one new subdirectory. Returns `{ "path": "<rel>/<name>" }`. Returns 400 if `name` contains a separator, NUL, or is `.`/`..`.
- `GET /api/validate?path=<rel>` — returns `{ "exists": bool, "is_dir": bool, "resolved_path": "<rel>", "writable": bool }`. `writable` for an existing path = whether the process can write to it; for a non-existent path = whether the nearest existing ancestor is writable (so a `mkdir -p` would succeed). Never 500s on a missing path — that's a successful query with `exists: false`. Returns 400 only on path-traversal attempts.
- `GET /api/autocomplete?prefix=<rel>` — splits `prefix` into `parent + last_segment`, lists `parent`'s subdirectories whose name contains `last_segment` (case-insensitive). Capped at 10. Returns `{ "matches": [{"name":"...", "path":"..."}] }`.
- `GET /api/search?q=<query>&limit=<n>` — recursive substring search over folder names. Returns `{ "matches": [...], "truncated": bool }`. Uses the 60s in-memory cache; cache key is `DOWNLOAD_ROOT`-rooted full tree; cache invalidated on `mkdir`. Cache is built lazily on first request.

### URL probing
- `POST /api/probe` — body `{ "url": "https://..." }`. Returns:
  ```json
  {
    "type": "hls_master" | "hls_media" | "direct" | "unknown",
    "variants": [
      { "url": "https://.../1080.m3u8", "width": 1920, "height": 1080,
        "bandwidth": 5000000, "codecs": "avc1.640028,mp4a.40.2",
        "frame_rate": 29.97, "label": "1920×1080 5.0 Mbps" }
    ],
    "duration_seconds": 3600.0
  }
  ```
  - `hls_master`: a master playlist with multiple variants. `variants` lists all, sorted by `bandwidth` descending (so index 0 is "best").
  - `hls_media`: an HLS playlist that is itself a media playlist (single variant). `variants` is empty.
  - `direct`: a non-HLS URL (regular `.mp4`, `.webm`, etc.). `variants` is empty.
  - `unknown`: couldn't fetch or recognize. `variants` is empty.
  Server hard-limits the fetched body to 256 KB; refuses non-http(s) schemes; uses a 10s connect+read timeout. Probe errors are returned as `{ "type": "unknown", "variants": [], "message": "..." }` (HTTP 200) so the UI doesn't break — the user can still submit, falling back to ffmpeg's default variant selection.

### Jobs
- `POST /api/downloads` — body:
  ```json
  {
    "url": "https://...",
    "selected_variant_url": "https://.../1080.m3u8",
    "filename": "office space",
    "extension": "mp4",
    "codec": "copy",
    "output_folder": "Movies/Office Space (1999)"
  }
  ```
  `selected_variant_url` is optional. When present and non-empty, ffmpeg is invoked with that URL instead of `url` — this is how the user pins a specific HLS variant. When absent, `url` is used as-is and ffmpeg's default HLS variant selection applies.

  Server-side actions:
  1. Validate `url` (and `selected_variant_url` if present) are http/https; sanitize `filename`; resolve `output_folder` (creates it if missing — `mkdir -p`); compute final `output_path = <output_folder>/<sanitized_filename>.<extension>` with `(n)` suffix collision handling.
  2. Build the ffmpeg command via `ffmpeg_command.build_command(input_url=selected_variant_url or url, ...)`.
  3. Insert into DB as `queued`, return the full job object.
- `GET /api/downloads?limit=50` — newest first.
- `GET /api/downloads/<id>` — one job.
- `DELETE /api/downloads/<id>` — if `running`, SIGTERM ffmpeg, mark `cancelled`. If terminal, remove row. Returns `{ "ok": true }`.
- `GET /api/downloads/<id>/events` — SSE. Events:
  - `event: progress\ndata: {"progress":42.5,"current_time_seconds":1530,"speed":"1.2x"}`
  - `event: status\ndata: {"status":"running","started_at":...}` and the terminal status events
  Connection closes when the job reaches a terminal state.
- `GET /api/events` — global SSE for the list view. Any job change emits `event: job\ndata: <full job>`.

### Health
- `GET /healthz` — `{ "ok": true, "version": "<sha>", "root_exists": true, "db_ok": true }`. Used by docker-compose healthchecks if desired.

### Job object shape
```json
{
  "id": "j_<ULID>",
  "url": "https://...",
  "selected_variant_url": "https://.../1080.m3u8",
  "selected_variant_label": "1920×1080 5.0 Mbps",
  "filename": "office space.mp4",
  "output_path": "Movies/Office Space (1999)/office space.mp4",
  "extension": "mp4",
  "codec": "copy",
  "command": "ffmpeg -reconnect 1 -i ... -c copy /downloads/Movies/...",
  "status": "queued|running|completed|failed|cancelled",
  "progress": 42.5,
  "duration_seconds": 3600.0,
  "current_time_seconds": 1530.0,
  "speed": "1.2x",
  "message": null,
  "created_at": 1715520000,
  "started_at": 1715520005,
  "finished_at": null
}
```

`selected_variant_url` and `selected_variant_label` are `null` when no variant was pinned (e.g. direct URL, or HLS where the user accepted "Auto (highest)").

`progress` is `null` when duration is unknown; the UI shows an indeterminate bar with elapsed time in that case.

## 5. URL probe & resolution picker

When the user enters or pastes a URL into the URL field, the client debounces for 500ms and calls `POST /api/probe`. The probe module fetches the URL (capped at 256 KB), checks if the body starts with `#EXTM3U`, and classifies it:

- **`hls_master`**: the body contains `#EXT-X-STREAM-INF:` lines. Each `#EXT-X-STREAM-INF:BANDWIDTH=...,RESOLUTION=...x...,CODECS="..."` plus its following URI is one variant. Relative URIs are resolved against the master URL. Variants are sorted by `BANDWIDTH` descending. The resolution dropdown shows `Auto (highest — 1920×1080 5.0 Mbps)` as the first/default option, followed by every variant.
- **`hls_media`**: starts with `#EXTM3U` but has `#EXTINF:` segments rather than `#EXT-X-STREAM-INF:` variants. Single resolution; no dropdown shown.
- **`direct`**: anything else (regular `.mp4`, `.webm`, `.mkv`, etc.). No dropdown shown.
- **`unknown`**: fetch failed, timeout, or unrecognized body. No dropdown shown; the user can still submit and ffmpeg will try.

### UI shape
The Resolution row appears between URL and Output Filename when (and only when) `variants` is non-empty:

```
URL: [https://.../master.m3u8                                  ]
Resolution: [ Auto (highest — 1920×1080 5.0 Mbps) ▾ ]
              ├ Auto (highest — 1920×1080 5.0 Mbps)
              ├ 1920×1080  5.0 Mbps  avc1
              ├ 1280×720   2.8 Mbps  avc1
              ├ 854×480    1.4 Mbps  avc1
              └ 640×360    0.7 Mbps  avc1
Output Filename: [...]
```

The dropdown stores the selected variant's URL in a hidden form field. "Auto (highest)" leaves it empty — the server treats that as "no variant pinned".

### Parser (rough sketch)
```python
def parse_master_playlist(body: str, base_url: str) -> list[Variant]:
    variants = []
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = parse_attrs(line[len("#EXT-X-STREAM-INF:"):])
        uri = next((l for l in lines[i+1:] if l and not l.startswith("#")), None)
        if not uri:
            continue
        variants.append(Variant(
            url=urljoin(base_url, uri),
            width=parse_resolution(attrs.get("RESOLUTION"))[0],
            height=parse_resolution(attrs.get("RESOLUTION"))[1],
            bandwidth=int(attrs.get("BANDWIDTH", 0)),
            codecs=attrs.get("CODECS", ""),
            frame_rate=float(attrs.get("FRAME-RATE", "0")) or None,
        ))
    return sorted(variants, key=lambda v: v.bandwidth, reverse=True)
```

Attribute parsing handles quoted strings (`CODECS="avc1.640028,mp4a.40.2"`) — a small regex, no full HLS library dependency.

## 6. Folder picker UX

The piece this rebuild specifically improves.

### Output Folder field on the main form
```
┌───────────────────────────────────────────────┬─────────┐
│ Movies/Office Space (1999)                  ✓ │ Browse  │
└───────────────────────────────────────────────┴─────────┘
       ↑                                    ↑
    editable input                  validation icon
    + autocomplete dropdown
```

- Input is editable. Pasting works. Typing works.
- **Live validation**, debounced 200ms → `/api/validate`:
  - `✓` green: exists and is a directory.
  - `⚠️` amber + tooltip "Will be created": doesn't exist yet. Submission is allowed — the server creates it.
  - `❌` red + tooltip: path traversal / invalid characters / writable=false.
- **Inline autocomplete**: dropdown under the field, populated from `/api/autocomplete?prefix=<value>`. Arrow keys / Enter to insert, Tab to insert without dismissing, Esc to dismiss. Selecting appends a `/` and re-triggers autocomplete so the user can drill down without typing every segment.

### Browse modal
Two modes, switched by a segmented control.

```
┌─ Select Output Folder ────────────────────  × ┐
│ [ Browse ▼ ]  [ Search ]                       │
│ Breadcrumb: Root / Movies                      │
│ ┌ Filter: [type to filter this folder...]    ┐ │
│ │ 📁 (500) Days of Summer (2009)              │ │
│ │ 📁 10 Things I Hate About You (1999)         │ │
│ │ ...                                          │ │
│ └─────────────────────────────────────────────┘ │
│ [ + new folder name........  ] [Create]         │
│           [ Cancel ]   [ Select This Folder ]   │
└────────────────────────────────────────────────┘
```

- **Browse mode**: today's tree + breadcrumb + create-folder, plus a client-side filter box that narrows the visible items.
- **Search mode**: a single search box. Debounced 250ms → `/api/search`. Each result row shows its full relative path; clicking a result switches back to Browse mode at that folder so the user can verify before selecting.

Keyboard:
- `↑/↓` — move focus between items
- `Enter` on a folder — enter it
- `Cmd/Ctrl+Enter` — select the current folder and close the modal
- `Esc` — cancel
- Pasting into either search box: if the pasted text validates as a folder path, surface a "Go to this folder" chip above the results.

### Visual style
Lightly modernized from today: same single-column, system-font, centered layout; subtler colors; dark-mode via `prefers-color-scheme: dark`. No framework — vanilla JS, vanilla CSS. The whole frontend is two files (`app.js`, `style.css`) plus one template.

## 7. ffmpeg orchestration

### Codec table
```python
CODEC_MAP = {
    # copy is the default. No -preset/-crf — true passthrough.
    "copy":  {"video": ["-c:v", "copy"], "audio": ["-c:a", "copy"], "extra": []},
    "h264":  {"video": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"],
              "audio": ["-c:a", "aac", "-b:a", "192k"], "extra": []},
    "h265":  {"video": ["-c:v", "libx265", "-preset", "veryfast", "-crf", "28"],
              "audio": ["-c:a", "aac", "-b:a", "192k"], "extra": []},
    "vp9":   {"video": ["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "31"],
              "audio": ["-c:a", "libopus", "-b:a", "128k"], "extra": []},
    "aac":   {"video": None, "audio": ["-c:a", "aac", "-b:a", "192k"], "extra": ["-vn"]},
    "mp3":   {"video": None, "audio": ["-c:a", "libmp3lame", "-q:a", "2"], "extra": ["-vn"]},
}
```

### Command shape
```
ffmpeg \
  -hide_banner -loglevel error \
  -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5     # only for http(s) inputs
  -i <input_url> \
  <video flags> <audio flags> <extra> \
  -bsf:a aac_adtstoasc                                          # only for *.m3u8 + audio=copy
  -progress pipe:1 -nostats \
  -y <output_path>
```

`<input_url>` is `selected_variant_url` if the user pinned a specific HLS variant, otherwise the original `url`.

`-progress pipe:1` emits clean `key=value` lines on stdout (one per ~500ms tick) including `out_time_us`, `speed`, and a final `progress=end`. The parser reads stdout line-by-line; stderr is captured into a rolling 4 KB buffer for the failure message.

### Duration discovery
Before launching ffmpeg, run:
```
ffprobe -v error -show_entries format=duration -of csv=p=0 <input_url>
```
with a 10s timeout against the same URL ffmpeg will use (the selected variant, if pinned). Success → store `duration_seconds` and compute progress as `current_time / duration * 100`. Failure / timeout → `duration_seconds = null`; UI shows an indeterminate bar plus elapsed-time text.

### Filename safety
- Strip path separators (`/`, `\`), NUL, and control characters from `filename`.
- Strip the user-supplied extension if they accidentally typed it (`.mp4`, `.MP4`).
- Append `.<extension>` from the dropdown.
- If `output_path` already exists, append ` (2)`, ` (3)`, ... until a free name is found.

### Cancellation
`DELETE /api/downloads/<id>` while running:
1. Mark the job as cancellation-requested (in-memory flag).
2. `proc.terminate()` (SIGTERM); wait up to 5s; `proc.kill()` if needed.
3. Best-effort `os.unlink(output_path)` for the partial file.
4. Mark `cancelled` in DB; emit final SSE event; clean up.

### URL safety
Only `http://` and `https://` URLs are accepted. `file://`, `pipe:`, `concat:`, and bare paths are rejected at the API layer — ffmpeg can otherwise be coerced into reading arbitrary files on the container.

## 8. Persistence

### Schema (`db.py`)
```sql
CREATE TABLE jobs (
  id                     TEXT PRIMARY KEY,    -- "j_" + 26-char ULID
  url                    TEXT NOT NULL,
  selected_variant_url   TEXT,
  selected_variant_label TEXT,
  filename               TEXT NOT NULL,
  output_path            TEXT NOT NULL,
  extension              TEXT NOT NULL,
  codec                  TEXT NOT NULL,
  command                TEXT NOT NULL,       -- pretty-printed argv, display only
  status                 TEXT NOT NULL,       -- queued|running|completed|failed|cancelled
  progress               REAL,                -- 0.0..100.0, NULL when duration unknown
  duration_seconds       REAL,
  current_time_seconds   REAL,
  speed                  TEXT,                -- e.g. "1.2x"
  message                TEXT,                -- failure stderr tail
  created_at             INTEGER NOT NULL,    -- unix seconds
  started_at             INTEGER,
  finished_at            INTEGER
);
CREATE INDEX jobs_created_at_idx ON jobs(created_at DESC);
```

WAL mode enabled (`PRAGMA journal_mode=WAL;`). One connection per request via Flask `g.db`. Foreign keys not needed (single table).

### Startup reconciliation
On app startup, before serving traffic:
1. `UPDATE jobs SET status='failed', message='Interrupted by restart', finished_at=:now WHERE status IN ('queued','running')`.
2. `DELETE FROM jobs WHERE status IN ('completed','failed','cancelled') AND finished_at < :now - JOB_RETENTION_DAYS * 86400`.

## 9. Security

- **Path traversal**: every relative path crosses `safe_path(rel)`, which resolves against `DOWNLOAD_ROOT` and rejects anything not strictly inside. `.resolve()` is used to defeat symlink escapes.
- **URL scheme**: only `http`/`https`. Anything else returns 400 from `POST /api/downloads`.
- **No shell**: `subprocess.Popen` is always called with an argv list, never `shell=True`. The ffmpeg `command` string stored in the DB is for display only.
- **No authentication**: by design. Document loudly in the README that this app must not be exposed to the public internet without an auth-providing reverse proxy in front.

## 10. Docker & deployment

### Dockerfile (final)
```dockerfile
FROM python:3.12-slim AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
RUN pip install --no-cache-dir .
COPY ffmpeg_downloader ./ffmpeg_downloader

ENV DOWNLOAD_ROOT=/downloads \
    DATA_DIR=/data \
    PORT=8000 \
    MAX_CONCURRENT_JOBS=2 \
    JOB_RETENTION_DAYS=30

VOLUME ["/downloads", "/data"]
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", "--worker-class", "gthread", "--workers", "1", "--threads", "16", \
     "--bind", "0.0.0.0:8000", "--timeout", "0", \
     "ffmpeg_downloader:create_app()"]
```

`tini` reaps zombie ffmpeg processes. `--timeout 0` is required because SSE responses are long-lived.

### docker-compose.example.yml
```yaml
services:
  ffmpeg-downloader:
    image: ghcr.io/ajthom90/ffmpeg-downloader:latest
    container_name: ffmpeg-downloader
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      - /path/to/your/media:/downloads
      - ./data:/data
    environment:
      - MAX_CONCURRENT_JOBS=2
```

### GitHub Actions

`.github/workflows/ci.yml`:
- Triggers: `pull_request`, `push` to `main`.
- Job: install Python 3.12, install with `pip install -e .[dev]`, run `ruff check`, `ruff format --check`, `pytest -q`.

`.github/workflows/docker.yml`:
- Triggers: `push` to `main` and tags matching `v*`.
- Job:
  - `docker/setup-qemu-action@v3`
  - `docker/setup-buildx-action@v3`
  - `docker/login-action@v3` to `ghcr.io` using `GITHUB_TOKEN`
  - `docker/metadata-action@v5` to compute tags: `latest` on `main`, the short SHA always, semver on tags.
  - `docker/build-push-action@v6` with `platforms: linux/amd64,linux/arm64` and the metadata tags.

## 11. Testing strategy

### Unit
- `ffmpeg_command.py`: for each codec + a representative URL (regular http, `.m3u8`), assert the produced argv list — flag-by-flag. Include a test where `input_url` differs from the user-supplied `url` (variant pinned).
- `filesystem.py`: `safe_path` rejects `..`, absolute paths, and symlinks pointing outside the root (test by creating a symlink in a tmpdir). `browse`, `mkdir`, `validate`, `autocomplete`, `search` happy paths and edge cases (empty path = root; non-existent path; path is a file not a dir).
- `probe.py`: a parametrized table of HLS master playlists (small captured `.m3u8` snippets) — including absolute and relative variant URIs, missing `RESOLUTION`, weird quoting in `CODECS`, BOM-prefixed bodies. Asserts the parsed variant list ordering and fields. Also tests classification of `hls_media` and `direct` URLs (no fetch — pass body bytes directly). The HTTP fetch is tested separately with a `wsgiref` local stub server.
- `db.py`: real temp-dir SQLite file; round-trip an insert, an update, a list, a delete.

### Integration
- Flask test client hits every endpoint. A `fake_ffmpeg.sh` shim is on `$PATH` (and `fake_ffprobe.sh`); the shim reads env vars to script its behavior — emit N seconds of progress lines, then exit 0 or 1. Job-manager tests run real jobs against this shim and assert SSE events, final DB state, and cancellation behavior. No mocking of the JobManager itself.

### Out of scope
- End-to-end browser tests. The frontend is small enough to test by hand; adding a Playwright suite for a personal app costs more than it pays.

## 12. Open questions / future work

None blocking the rebuild. Potential follow-ups (not in scope here):
- yt-dlp integration for sites that don't expose a direct stream URL.
- Multi-user / shared deployment with auth.
- Browser-side preview for partial downloads.
- A "templates" feature (save common output-folder + codec combos).
