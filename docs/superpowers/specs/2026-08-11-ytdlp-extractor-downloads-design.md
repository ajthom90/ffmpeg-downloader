# yt-dlp extractor downloads — Design

Add support for downloading videos from YouTube and other yt-dlp–supported sites into the existing ffmpeg-downloader app: paste a URL, pick quality/format, download to the same folder picker / job history / SSE progress flow.

- **Parent design**: `docs/superpowers/specs/2026-05-12-ffmpeg-downloader-design.md`
- **Repo**: <https://github.com/ajthom90/ffmpeg-downloader>
- **Date**: 2026-08-11

## 1. Goals & non-goals

### Goals
- Paste a YouTube (or any yt-dlp-supported) URL → probe available qualities/formats → download to the chosen subfolder under `DOWNLOAD_ROOT`.
- Auto-fill the output filename from the video title when the filename field is empty (user can still edit).
- Quality picker with human-friendly options (e.g. “Best available”, “1080p”, “720p”, “Audio only”), default **Best available**.
- Reuse the existing job list, cancel, SSE progress, and folder picker UX.
- Leave the existing HLS / direct **ffmpeg** path unchanged.

### Non-goals
- Cookies, browser auth, age-gated or members-only content (public videos only in v1).
- Per-language audio or subtitle track pickers.
- Playlists, channels, or multi-video queues — reject with a clear error; user must paste a single video URL.
- Replacing ffmpeg for plain media files or HLS (`.m3u8`) URLs.
- Forcing re-encode via the Codec control for extractor jobs (codec is ignored / disabled in the UI for those jobs).

This revises the parent design’s non-goal of “no yt-dlp integration.” Extractor-site downloads become a first-class second backend.

## 2. Architecture

Dual pipeline: **ffmpeg** for HLS/direct media; **yt-dlp** for extractor page URLs.

```
Browser ──POST /api/probe──> probe router
                              ├─ HLS / direct media  → probe.py (existing)
                              └─ extractor page URL  → ytdlp.py (yt-dlp metadata)
                                    type: "extractor" | "unsupported"

Browser ──POST /api/downloads──> JobManager.submit
                              ├─ backend: "ffmpeg" (default) → existing ffmpeg subprocess
                              └─ backend: "ytdlp"            → yt-dlp subprocess + progress parse
```

### Module boundaries

| Module | Change | Responsibility |
|---|---|---|
| `ytdlp.py` (new) | New | Detect/list formats via yt-dlp; build argv; parse progress lines. Binary path injected for tests. |
| `probe.py` | Minimal | Unchanged HLS parsing. Routes orchestrate “try extractor vs HLS” order. |
| `jobs.py` | Extend | `JobSpec` gains `backend`, `format_selector`, `format_label`. Run path branches for yt-dlp. |
| `routes.py` | Extend | Probe returns extractor shape; download accepts `backend` + `format_selector`. |
| `config.py` | Extend | `YTDLP_BIN` (default `yt-dlp`). |
| `db.py` | Extend | Add explicit SQLite columns `backend`, `format_selector`, `format_label` on jobs; backfill `backend = 'ffmpeg'` for existing rows. |
| Docker / deps | Extend | Install `yt-dlp` in the image; document `YTDLP_BIN`. |
| UI (`app.js`, `index.html`) | Extend | On extractor probe: quality select, auto-fill title, disable codec; keep extension. |

### Probe decision order
1. URL missing or scheme not `http`/`https` → 400 (unchanged).
2. If the URL path/query clearly indicates HLS (`.m3u8`) or a common direct media extension (`.mp4`, `.webm`, `.mkv`, `.mp3`, `.m4a`, `.ts`, etc.) → existing `probe.py` only.
3. Otherwise run yt-dlp metadata extraction (no download):
   - Single video match → `type: "extractor"` with title, duration, grouped formats.
   - Playlist / channel / multi-entry → `type: "unsupported"` with message: paste a single video URL.
   - yt-dlp recognizes the site but cannot extract (private, age-gate, geo-block, etc.) → `type: "unsupported"` with the error message (do **not** fall through to ffmpeg; submit stays disabled until the URL changes).
   - No extractor match / yt-dlp not useful for this URL → fall through to existing `probe.py` (HLS/direct/unknown).

### Format grouping
yt-dlp returns many format rows. The server groups them into practical options:

| Option id | Label | Format selector (illustrative) |
|---|---|---|
| `best` | Best available | `bv*+ba/b` |
| `2160` / `1440` / `1080` / `720` / `480` / `360` | `{height}p` | `bv*[height<=H]+ba/b[height<=H]` — only include heights that actually exist in the format list |
| `audio` | Audio only | `ba/b` |

- Default selection: **Best available**.
- Prefer storing a **format selector string**, not a single format ID, so video+audio merge continues to work.
- Labels stay short; optional bitrate/ext can appear in the label if cheap to derive, but height + “Best” / “Audio only” is enough for v1.

### Download behavior (yt-dlp)
- Output path: same collision-safe `output_folder` + sanitized `filename` + user-chosen `extension`.
- Invoke yt-dlp with `-f <format_selector>`, `-o <absolute output path>`, merge/remux as needed so the final file uses the requested extension when possible (`--merge-output-format` / remux when appropriate).
- **Codec field is ignored** for `backend: "ytdlp"`. UI disables the codec select and shows a short hint.
- **Extension remains user-controlled** (mp4 / mkv / webm / m4a / …). Audio-only selection may suggest `m4a` in the UI but does not force it.

### Progress
- Run yt-dlp with machine-parseable progress (e.g. `--newline` and/or `--progress-template`).
- Map percent (and speed when available) into the same job fields: `progress`, `speed`, `message`, `current_time_seconds` / `duration_seconds` when known.
- Publish via existing per-job pubsub so SSE and job cards need no new event types.
- Cancel: SIGTERM the yt-dlp process group (mirror ffmpeg cancel).

### Concurrency
- Extractor jobs share the same `ThreadPoolExecutor` / `MAX_CONCURRENT_JOBS` as ffmpeg jobs. No separate pool in v1.

## 3. Configuration

| Env var | Default | Purpose |
|---|---|---|
| `YTDLP_BIN` | `yt-dlp` | Path to the yt-dlp binary. Tests point this at a shim script. |

Startup: if `YTDLP_BIN` is missing from `PATH` / not executable, log a warning; probe of extractor URLs returns a clear error. Do not hard-fail process start solely because yt-dlp is absent (keeps ffmpeg-only deployments working), **except** in Docker images where yt-dlp is expected to be installed as a packaging invariant.

## 4. HTTP API

### `POST /api/probe` — extended responses

Existing `hls_master` / `hls_media` / `direct` / `unknown` shapes unchanged (`variants` array as today).

**Extractor (single video):**
```json
{
  "type": "extractor",
  "title": "Video Title Here",
  "extractor": "youtube",
  "webpage_url": "https://www.youtube.com/watch?v=…",
  "duration_seconds": 612.0,
  "formats": [
    {
      "id": "best",
      "label": "Best available",
      "format_selector": "bv*+ba/b",
      "height": null,
      "ext": "mp4",
      "is_audio_only": false
    },
    {
      "id": "1080",
      "label": "1080p",
      "format_selector": "bv*[height<=1080]+ba/b[height<=1080]",
      "height": 1080,
      "ext": "mp4",
      "is_audio_only": false
    },
    {
      "id": "audio",
      "label": "Audio only",
      "format_selector": "ba/b",
      "height": null,
      "ext": "m4a",
      "is_audio_only": true
    }
  ],
  "variants": []
}
```

**Unsupported multi-entry (playlist/channel):**
```json
{
  "type": "unsupported",
  "message": "Playlists and channels are not supported. Paste a single video URL.",
  "variants": [],
  "formats": []
}
```

### `POST /api/downloads` — extended body

```json
{
  "url": "https://…",
  "filename": "…",
  "extension": "mp4",
  "codec": "copy",
  "output_folder": "…",
  "backend": "ytdlp",
  "format_selector": "bv*+ba/b",
  "format_label": "Best available"
}
```

Rules:
- `backend` optional; default `"ffmpeg"`. Only `"ytdlp"` selects the extractor path; the server does not infer backend from `format_selector` alone. The UI always sends `backend: "ytdlp"` when an extractor probe is active.
- For `ytdlp`: require `url` (http/https); ignore `selected_variant_url`, `audio_urls`, `subtitle_urls`, and `codec`. Default `format_selector` to `bv*+ba/b` if omitted.
- For `ffmpeg`: existing validation and multi-stream behavior unchanged.

### Job object / SSE
Add optional fields returned on job JSON:
- `backend`: `"ffmpeg"` | `"ytdlp"`
- `format_selector`: string or null
- `format_label`: string or null

SSE event types and payload shapes remain unchanged (`progress`, `status`, terminal states).

## 5. UI

- Same single form; no separate “YouTube mode” page.
- On probe `type === "extractor"`:
  - Show quality `<select>` populated from `formats` (reuse the resolution group pattern; label it “Quality” when in extractor mode).
  - Auto-fill filename from `title` when the filename field is empty.
  - Disable codec select; optional hint that codec does not apply to site downloads.
  - Keep extension select enabled.
- On probe `type === "unsupported"`: show `message` in the URL hint; disable submit until the URL changes.
- On HLS / direct: existing resolution / audio / subtitle / codec behavior.
- Submit includes `backend: "ytdlp"` and the chosen `format_selector` / `format_label` when in extractor mode.

## 6. Data model

Persist enough to re-display history correctly after restart:
- `backend` (default `ffmpeg` for backfill of existing rows)
- `format_selector` (nullable)
- `format_label` (nullable)

Add explicit SQLite columns in `db.py` (same create/migrate pattern the app already uses for the jobs table). Existing rows remain valid with `backend = 'ffmpeg'`.

## 7. Error handling

| Case | Behavior |
|---|---|
| yt-dlp missing / not executable | Probe returns clear message; jobs with `backend: ytdlp` fail immediately with the same message. |
| Private / age-restricted / geo-blocked | Job `failed`; `message` includes truncated yt-dlp stderr. |
| Playlist / channel URL | Probe `unsupported`; no job created from a normal UI submit. |
| Cancel while running | SIGTERM process group; status `cancelled`. |
| Disk full / write error | Job `failed` with message. |

Do not log full cookies or credentials (none in v1). Truncate long stderr in the job message (same order of magnitude as ffmpeg failures today).

## 8. Docker & packaging

- Install `yt-dlp` in the Docker image (pip or distro package; prefer a method that is easy to refresh when extractors break).
- Keep `ffmpeg` / `ffprobe` for HLS path and for yt-dlp’s merge/remux.
- Document `YTDLP_BIN` in README env table.
- README feature bullet: paste YouTube / site URLs, pick quality, download.

## 9. Testing

No live network calls to YouTube or other sites in CI.

| Layer | Approach |
|---|---|
| `ytdlp.py` format grouping | Unit tests with fixture JSON (captured `yt-dlp -J` shape, sanitized). |
| Job runner | `fake_ytdlp.sh` shim printing progress lines; assert output path, argv contains `-f` and output path. |
| API | Probe returns `extractor` when shim succeeds; `unsupported` for multi-entry fixture; download creates job with `backend: ytdlp`. |
| Regression | Existing HLS/ffmpeg tests still pass without yt-dlp behavior changes. |

## 10. Implementation outline (for the plan)

1. Config + Dockerfile + dependency wiring for `yt-dlp`.
2. `ytdlp.py`: metadata extract, format grouping, argv builder, progress parser.
3. DB columns + job model / JobManager branch.
4. Routes: probe orchestration + download fields.
5. UI: quality select, title auto-fill, codec disable, submit payload.
6. Tests + README.
7. Manual smoke: public YouTube URL best + 720p + audio-only against a real binary (outside CI).

## 11. Open decisions (resolved in brainstorming)

| Decision | Choice |
|---|---|
| Scope of sites | Any yt-dlp-supported site, not YouTube-only |
| Quality UX | Grouped quality + format options, not raw format IDs |
| Filename | Auto-fill from title when empty |
| Auth | Public only; no cookies file in v1 |
| Pipeline | Dual backend (Approach A): yt-dlp for extractors, ffmpeg for HLS/direct |
| Playlist URLs | Reject with clear error |
| Default quality | Best available |
| Codec / extension | Keep extension; hide/ignore codec for extractor jobs |
| Concurrency | Shared job pool with ffmpeg |
|
