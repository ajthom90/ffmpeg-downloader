from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class ConfigError(RuntimeError):
    """Fatal misconfiguration discovered at startup."""


@dataclass(frozen=True)
class Config:
    download_root: Path
    data_dir: Path
    port: int
    max_concurrent_jobs: int
    job_retention_days: int
    search_cache_ttl_seconds: int
    search_result_limit: int
    ffmpeg_bin: str
    ffprobe_bin: str
    ytdlp_bin: str
    ytdlp_js_runtime: str

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Config:
        e = env if env is not None else os.environ
        download_root = Path(e.get("DOWNLOAD_ROOT", "/downloads"))
        data_dir = Path(e.get("DATA_DIR", "/data"))

        if not download_root.exists():
            raise ConfigError(f"DOWNLOAD_ROOT does not exist: {download_root}")
        if not download_root.is_dir():
            raise ConfigError(f"DOWNLOAD_ROOT is not a directory: {download_root}")
        if not os.access(download_root, os.W_OK):
            raise ConfigError(f"DOWNLOAD_ROOT is not writable: {download_root}")

        data_dir.mkdir(parents=True, exist_ok=True)
        if not os.access(data_dir, os.W_OK):
            raise ConfigError(f"DATA_DIR is not writable: {data_dir}")

        return cls(
            download_root=download_root.resolve(),
            data_dir=data_dir.resolve(),
            port=int(e.get("PORT", "8000")),
            max_concurrent_jobs=int(e.get("MAX_CONCURRENT_JOBS", "2")),
            job_retention_days=int(e.get("JOB_RETENTION_DAYS", "30")),
            search_cache_ttl_seconds=int(e.get("SEARCH_CACHE_TTL_SECONDS", "60")),
            search_result_limit=int(e.get("SEARCH_RESULT_LIMIT", "50")),
            ffmpeg_bin=e.get("FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=e.get("FFPROBE_BIN", "ffprobe"),
            ytdlp_bin=e.get("YTDLP_BIN", "yt-dlp"),
            ytdlp_js_runtime=e.get("YTDLP_JS_RUNTIME", "deno"),
        )
