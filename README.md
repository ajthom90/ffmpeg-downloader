# ffmpeg-downloader

A small self-hosted web app that wraps `ffmpeg` to download media — especially HLS / `.m3u8` streams — into a configured folder on disk.

> **Security:** No authentication. Sit behind a reverse proxy on a trusted network. **Do not expose to the public internet.**

## Features

- Submit a URL, pick a folder, hit Download.
- Paste a YouTube / site URL (yt-dlp); pick quality; download like any other job.
- HLS master playlists are inspected automatically; choose a specific resolution before submitting.
- Default codec is `copy` — pure passthrough, no transcode. Optional transcoding presets for h264 / h265 / vp9 / aac / mp3.
- Five ways to pick the output folder:
  - Browse a tree
  - Type-to-filter the visible folder
  - Path autocomplete in the field itself
  - Paste a path with live validation
  - Recursive search across the whole tree
- Real-time progress via Server-Sent Events.
- Job history persisted in SQLite, survives container restarts.

## Quick start

```bash
docker run -d --name ffmpeg-downloader \
  -p 8000:8000 \
  -v /path/to/your/media:/downloads \
  -v $(pwd)/data:/data \
  --restart unless-stopped \
  ghcr.io/ajthom90/ffmpeg-downloader:latest
```

Then open <http://localhost:8000>.

A reference `docker-compose.example.yml` is included.

## Environment variables

| Var | Default | What it does |
|---|---|---|
| `DOWNLOAD_ROOT` | `/downloads` | The media root mounted into the container. |
| `DATA_DIR` | `/data` | Where `jobs.db` lives. |
| `PORT` | `8000` | Bind port. |
| `MAX_CONCURRENT_JOBS` | `2` | How many ffmpeg processes can run at once. |
| `JOB_RETENTION_DAYS` | `30` | Old finished jobs are pruned on startup. |
| `SEARCH_CACHE_TTL_SECONDS` | `60` | TTL on the recursive-search folder cache. |
| `SEARCH_RESULT_LIMIT` | `50` | Max recursive-search results. |
| `FFMPEG_BIN` / `FFPROBE_BIN` | `ffmpeg` / `ffprobe` | Override the binary paths. |
| `YTDLP_BIN` | `yt-dlp` | Path to the yt-dlp binary for site downloads. |
| `YTDLP_JS_RUNTIME` | `deno` | JS runtime for YouTube (`deno`, `node`, or `deno:/path/to/deno`). |

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .[dev]

# Tests
pytest -q

# Lint / format
ruff check .
ruff format .

# Dev server
mkdir -p /tmp/ffd-root /tmp/ffd-data
DOWNLOAD_ROOT=/tmp/ffd-root DATA_DIR=/tmp/ffd-data \
  flask --app ffmpeg_downloader run --debug --port 5050
```

## Architecture (short)

Flask app, one gunicorn worker (gthread, 16 threads), in-process `JobManager` running ffmpeg via `subprocess.Popen` with `-progress pipe:1` (and yt-dlp for site page URLs). SSE streams pull from a per-job pubsub. SQLite (WAL) persists the job table. See `docs/superpowers/specs/` for the full design.

Site downloads use [yt-dlp](https://github.com/yt-dlp/yt-dlp). YouTube now requires a JavaScript runtime to solve player challenges; the image ships [Deno](https://deno.land/) (yt-dlp's default). Override with `YTDLP_JS_RUNTIME` if you use Node instead. Extractors break when sites change — rebuild/pull a fresh image periodically. Public videos only (no cookies) in this version. Playlists/channels are rejected; paste a single video URL.

## License

MIT — see `LICENSE`.
