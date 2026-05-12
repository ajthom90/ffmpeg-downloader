from __future__ import annotations

import contextlib
import os
import re
import subprocess
import threading
import time
from collections import deque
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


def _parse_progress_line(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


def _probe_duration(ffprobe_bin: str, url: str, timeout: float = 10.0) -> float | None:
    try:
        proc = subprocess.run(
            [
                ffprobe_bin,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    raw = proc.stdout.strip().splitlines()
    if not raw:
        return None
    try:
        return float(raw[0])
    except ValueError:
        return None


def _read_stderr_in_background(proc: subprocess.Popen) -> callable:
    """Drain stderr into a rolling buffer; return a getter for the latest 4KB."""
    assert proc.stderr is not None
    buf: deque[str] = deque(maxlen=4096)

    def _reader():
        assert proc.stderr is not None
        for chunk in proc.stderr:
            for ch in chunk:
                buf.append(ch)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    def _get_tail() -> str:
        t.join(timeout=1.0)
        return "".join(buf).strip()

    return _get_tail


def _run_job_impl(self: JobManager, job_id: str, argv: list[str], output_path: str) -> None:
    """The real body of _run_job, attached below."""
    job_row = self._db.get_job(job_id)
    if job_row is None:
        return
    input_url = job_row["selected_variant_url"] or job_row["url"]
    duration = _probe_duration(self._ffprobe_bin, input_url)
    now = int(time.time())
    with self._db_lock:
        self._db.update_job(job_id, status="running", started_at=now, duration_seconds=duration)
    self._publish_status(job_id, "running")

    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
    except FileNotFoundError as e:
        self._finalize(job_id, "failed", message=f"ffmpeg not found: {e}")
        return

    with self._lock:
        self._procs[job_id] = proc

    stderr_tail = _read_stderr_in_background(proc)

    current_time = None
    speed = None
    try:
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            kv = _parse_progress_line(raw_line.strip())
            if not kv:
                continue
            key, value = kv
            if key == "out_time_us":
                try:
                    current_time = int(value) / 1_000_000.0
                except ValueError:
                    continue
            elif key == "out_time_ms":  # older ffmpeg
                try:
                    current_time = int(value) / 1000.0
                except ValueError:
                    continue
            elif key == "speed":
                speed = value
            elif key == "progress" and value == "end":
                break

            if current_time is not None:
                pct = None
                if duration and duration > 0:
                    pct = min(100.0, max(0.0, current_time / duration * 100.0))
                with self._db_lock:
                    self._db.update_job(
                        job_id,
                        progress=pct,
                        current_time_seconds=current_time,
                        speed=speed,
                    )
                self._publish_progress(job_id, pct, current_time, speed)
    finally:
        proc.wait()
        with self._lock:
            self._procs.pop(job_id, None)

    if job_id in self._cancelled:
        with contextlib.suppress(OSError):
            os.unlink(output_path)
        self._finalize(job_id, "cancelled", message=None)
        return
    if proc.returncode == 0:
        self._finalize(job_id, "completed", message=None, progress=100.0)
    else:
        tail = stderr_tail()
        self._finalize(job_id, "failed", message=tail)


def _finalize(
    self: JobManager,
    job_id: str,
    status: str,
    *,
    message: str | None,
    progress: float | None = None,
) -> None:
    fields: dict[str, Any] = {
        "status": status,
        "finished_at": int(time.time()),
        "message": message,
    }
    if progress is not None:
        fields["progress"] = progress
    with self._db_lock:
        self._db.update_job(job_id, **fields)
    self._publish_status(job_id, status)


def _publish_status(self: JobManager, job_id: str, status: str) -> None:
    """No-op placeholder; Task 14 attaches the real pubsub."""
    return


def _publish_progress(
    self: JobManager,
    job_id: str,
    progress: float | None,
    current_time: float | None,
    speed: str | None,
) -> None:
    return


JobManager._run_job = _run_job_impl  # type: ignore[assignment]
JobManager._finalize = _finalize  # type: ignore[assignment]
JobManager._publish_status = _publish_status  # type: ignore[assignment]
JobManager._publish_progress = _publish_progress  # type: ignore[assignment]


import queue as _queue  # noqa: E402


class _Pubsub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._per_job: dict[str, list[_queue.Queue]] = {}
        self._global: list[_queue.Queue] = []

    def subscribe_job(self, job_id: str) -> _queue.Queue:
        q: _queue.Queue = _queue.Queue()
        with self._lock:
            self._per_job.setdefault(job_id, []).append(q)
        return q

    def subscribe_global(self) -> _queue.Queue:
        q: _queue.Queue = _queue.Queue()
        with self._lock:
            self._global.append(q)
        return q

    def unsubscribe_job(self, job_id: str, q: _queue.Queue) -> None:
        with self._lock:
            if job_id in self._per_job and q in self._per_job[job_id]:
                self._per_job[job_id].remove(q)

    def unsubscribe_global(self, q: _queue.Queue) -> None:
        with self._lock:
            if q in self._global:
                self._global.remove(q)

    def publish_job(self, job_id: str, event: str, data: dict) -> None:
        envelope = {"event": event, "data": data}
        with self._lock:
            subscribers = list(self._per_job.get(job_id, []))
            global_subs = list(self._global) if event == "status" else []
        for q in subscribers:
            q.put(envelope)
        for q in global_subs:
            q.put({"event": "job", "data": data})


def _attach_pubsub() -> None:
    original_init = JobManager.__init__

    def __init__(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self._pubsub = _Pubsub()

    def subscribe(self, job_id: str) -> _queue.Queue:
        return self._pubsub.subscribe_job(job_id)

    def subscribe_global(self) -> _queue.Queue:
        return self._pubsub.subscribe_global()

    def unsubscribe(self, job_id: str, q: _queue.Queue) -> None:
        self._pubsub.unsubscribe_job(job_id, q)

    def unsubscribe_global(self, q: _queue.Queue) -> None:
        self._pubsub.unsubscribe_global(q)

    def _publish_status(self, job_id: str, status: str) -> None:
        with self._db_lock:
            row = self._db.get_job(job_id)
        if row:
            self._pubsub.publish_job(job_id, "status", row)

    def _publish_progress(self, job_id: str, progress, current_time, speed) -> None:
        payload = {
            "progress": progress,
            "current_time_seconds": current_time,
            "speed": speed,
        }
        self._pubsub.publish_job(job_id, "progress", payload)

    JobManager.__init__ = __init__  # type: ignore[assignment]
    JobManager.subscribe = subscribe  # type: ignore[attr-defined]
    JobManager.subscribe_global = subscribe_global  # type: ignore[attr-defined]
    JobManager.unsubscribe = unsubscribe  # type: ignore[attr-defined]
    JobManager.unsubscribe_global = unsubscribe_global  # type: ignore[attr-defined]
    JobManager._publish_status = _publish_status  # type: ignore[assignment]
    JobManager._publish_progress = _publish_progress  # type: ignore[assignment]


_attach_pubsub()


def _cancel(self: JobManager, job_id: str) -> None:
    with self._lock:
        proc = self._procs.get(job_id)
        if not proc:
            # Either not running, or finished. Mark cancelled only if still queued.
            with self._db_lock:
                row = self._db.get_job(job_id)
            if row and row["status"] == "queued":
                self._cancelled.add(job_id)
                self._finalize(job_id, "cancelled", message=None)
            return
        self._cancelled.add(job_id)
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        proc.wait(timeout=2.0)


JobManager.cancel = _cancel  # type: ignore[assignment]
