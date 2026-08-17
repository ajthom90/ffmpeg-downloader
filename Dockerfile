# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg ca-certificates tini curl unzip \
 && ARCH="$(uname -m)" \
 && case "$ARCH" in \
      x86_64) DENO_ARCH=x86_64-unknown-linux-gnu ;; \
      aarch64) DENO_ARCH=aarch64-unknown-linux-gnu ;; \
      *) echo "unsupported arch: $ARCH" >&2; exit 1 ;; \
    esac \
 && curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_ARCH}.zip" -o /tmp/deno.zip \
 && unzip -o /tmp/deno.zip -d /usr/local/bin \
 && chmod +x /usr/local/bin/deno \
 && rm /tmp/deno.zip \
 && deno --version \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir . "yt-dlp[default]"

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
