"""ffmpeg-downloader: a self-hosted ffmpeg wrapper for m3u8 downloads."""

from __future__ import annotations

import os
import time
from typing import Any

from flask import Flask

from .config import Config
from .db import Database
from .filesystem import RootedFS
from .jobs import JobManager

__version__ = "0.1.0"


def create_app(config_overrides: dict[str, Any] | None = None) -> Flask:
    overrides = dict(config_overrides or {})
    testing = bool(overrides.pop("TESTING", False))
    env_view = {**os.environ}
    for key, value in overrides.items():
        env_view[key] = str(value)
    config = Config.from_env(env_view)

    db = Database.open(config.data_dir / "jobs.db")
    db.reconcile_on_startup(now=int(time.time()), retention_days=config.job_retention_days)
    fs = RootedFS(config.download_root, cache_ttl=config.search_cache_ttl_seconds)
    jobs = JobManager(
        db=db,
        fs=fs,
        ffmpeg_bin=config.ffmpeg_bin,
        ffprobe_bin=config.ffprobe_bin,
        ytdlp_bin=config.ytdlp_bin,
        ytdlp_js_runtime=config.ytdlp_js_runtime,
        max_concurrent_jobs=config.max_concurrent_jobs,
    )

    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.extensions["config"] = config
    app.extensions["db"] = db
    app.extensions["fs"] = fs
    app.extensions["jobs"] = jobs

    @app.get("/healthz")
    def _healthz():
        return {
            "ok": True,
            "version": __version__,
            "root_exists": config.download_root.is_dir(),
            "db_ok": db.get_job("__never__") is None,  # cheap query
        }

    # Routes added in later tasks.
    from . import routes

    routes.register(app)

    return app
