"""ffmpeg-downloader: a self-hosted ffmpeg wrapper for m3u8 downloads."""

from __future__ import annotations

__version__ = "0.1.0"


def create_app(config_overrides: dict | None = None):
    """Flask app factory. Real wiring is added in later tasks."""
    from flask import Flask

    app = Flask(__name__)
    app.config["TESTING"] = bool(config_overrides and config_overrides.get("TESTING"))

    @app.get("/healthz")
    def _healthz():
        return {"ok": True}

    return app
