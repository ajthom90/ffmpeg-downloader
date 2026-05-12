from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ulid import ULID

from .db import Database
from .ffmpeg_command import build_command, pretty_command
from .filesystem import RootedFS

_BAD_FILENAME_CHARS = re.compile(r'[\\/\x00-\x1f<>:"|?*]')


@dataclass
class JobSpec:
    url: str
    selected_variant_url: str | None
    selected_variant_label: str | None
    filename: str
    extension: str
    codec: str
    output_folder: str


def _sanitize_filename(name: str, extension: str) -> str:
    cleaned = _BAD_FILENAME_CHARS.sub("", name).strip()
    if not cleaned:
        cleaned = "download"
    lower_ext = f".{extension.lower()}"
    if cleaned.lower().endswith(lower_ext):
        cleaned = cleaned[: -len(lower_ext)]
    return cleaned


def _resolve_collision(folder: Path, filename: str, extension: str) -> str:
    """Return a filename inside `folder` that does not yet exist."""
    candidate = folder / f"{filename}.{extension}"
    if not candidate.exists():
        return f"{filename}.{extension}"
    n = 2
    while True:
        candidate = folder / f"{filename} ({n}).{extension}"
        if not candidate.exists():
            return f"{filename} ({n}).{extension}"
        n += 1


class JobManager:
    def __init__(
        self,
        *,
        db: Database,
        fs: RootedFS,
        ffmpeg_bin: str,
        ffprobe_bin: str,
        max_concurrent_jobs: int,
    ):
        self._db = db
        self._fs = fs
        self._ffmpeg_bin = ffmpeg_bin
        self._ffprobe_bin = ffprobe_bin
        self._executor = ThreadPoolExecutor(max_workers=max_concurrent_jobs)
        self._lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._procs: dict[str, Any] = {}  # job_id -> Popen
        self._cancelled: set[str] = set()

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)

    # ---------- submit ----------

    def submit(self, spec: JobSpec) -> dict:
        job_id = f"j_{ULID()}"
        # Resolve / create the output folder.
        folder_rel = spec.output_folder.strip().lstrip("/")
        if folder_rel:
            # Make sure it's inside root.
            self._fs.safe_path(folder_rel)
            folder_abs = (self._fs.root / folder_rel).resolve()
            folder_abs.mkdir(parents=True, exist_ok=True)
        else:
            folder_abs = self._fs.root

        clean_name = _sanitize_filename(spec.filename, spec.extension)
        filename = _resolve_collision(folder_abs, clean_name, spec.extension)
        rel_output = self._fs.rel(folder_abs / filename) if folder_rel else filename

        input_url = spec.selected_variant_url or spec.url
        argv = build_command(
            ffmpeg_bin=self._ffmpeg_bin,
            input_url=input_url,
            output_path=str(folder_abs / filename),
            codec=spec.codec,
            extension=spec.extension,
        )
        cmd_str = pretty_command(argv)
        now = int(time.time())
        row = {
            "id": job_id,
            "url": spec.url,
            "selected_variant_url": spec.selected_variant_url,
            "selected_variant_label": spec.selected_variant_label,
            "filename": filename,
            "output_path": rel_output,
            "extension": spec.extension,
            "codec": spec.codec,
            "command": cmd_str,
            "status": "queued",
            "progress": None,
            "duration_seconds": None,
            "current_time_seconds": None,
            "speed": None,
            "message": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
        }
        with self._db_lock:
            self._db.insert_job(row)
        self._submit_to_executor(job_id, argv, str(folder_abs / filename))
        return row

    def _submit_to_executor(self, job_id: str, argv: list[str], output_path: str) -> None:
        """Schedule the job for execution. Overridden in tests to be a no-op."""
        self._executor.submit(self._run_job, job_id, argv, output_path)

    def _run_job(self, job_id: str, argv: list[str], output_path: str) -> None:
        """Filled in by Task 13."""
        raise NotImplementedError
