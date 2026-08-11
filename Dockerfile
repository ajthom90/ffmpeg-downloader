# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir . yt-dlp

COPY ffmpeg_downloader ./ffmpeg_downloader

ENV DOWNLOAD_ROOT=/downloads \
    DATA_DIR=/data \
    PORT=8000 \
    MAX_CONCURRENT_JOBS=2 \
    JOB_RETENTION_DAYS=30 \
    PYTHONUNBUFFERED=1

VOLUME ["/downloads", "/data"]
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["gunicorn", \
     "--worker-class", "gthread", \
     "--workers", "1", \
     "--threads", "16", \
     "--bind", "0.0.0.0:8000", \
     "--timeout", "0", \
     "ffmpeg_downloader:create_app()"]
