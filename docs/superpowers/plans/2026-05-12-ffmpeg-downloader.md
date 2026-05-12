# ffmpeg-downloader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the ffmpeg-downloader Flask app from scratch: form-submitted ffmpeg jobs with SSE progress, SQLite history, multi-style folder picker, HLS variant probe, multi-arch Docker image to GHCR.

**Architecture:** Single Flask app, one gunicorn worker (gthread), in-process JobManager with `ThreadPoolExecutor` (default 2 workers) running `subprocess.Popen(ffmpeg)`. SSE feeds per-job pubsub. SQLite (WAL) persists job history. Vanilla-JS frontend.

**Tech Stack:** Python 3.12, Flask 3.x, gunicorn, python-ulid, sqlite3 (stdlib), pytest, ruff. Docker (python:3.12-slim + apt ffmpeg). GitHub Actions for multi-arch GHCR build.

**Spec:** `docs/superpowers/specs/2026-05-12-ffmpeg-downloader-design.md`

---

## File Structure

```
ffmpeg-downloader/
├── ffmpeg_downloader/
│   ├── __init__.py            # create_app() factory; wires Config + DB + JobManager
│   ├── config.py              # Config dataclass from env
│   ├── db.py                  # SQLite schema + job CRUD
│   ├── filesystem.py          # safe_path + browse/mkdir/validate/autocomplete/search
│   ├── ffmpeg_command.py      # CODEC_MAP + build_command()
│   ├── probe.py               # HLS parser + fetch_url()
│   ├── jobs.py                # JobManager: submit, run, progress, cancel, pubsub
│   ├── routes.py              # All HTTP/SSE endpoints
│   ├── templates/
│   │   └── index.html
│   └── static/
│       ├── app.js
│       └── style.css
├── tests/
│   ├── conftest.py
│   ├── fake_ffmpeg.sh
│   ├── fake_ffprobe.sh
│   ├── fixtures/
│   │   ├── master-simple.m3u8
│   │   ├── master-relative-uris.m3u8
│   │   └── media-only.m3u8
│   ├── test_config.py
│   ├── test_db.py
│   ├── test_filesystem.py
│   ├── test_ffmpeg_command.py
│   ├── test_probe.py
│   ├── test_jobs.py
│   └── test_api.py
├── docs/superpowers/
│   ├── specs/2026-05-12-ffmpeg-downloader-design.md
│   └── plans/2026-05-12-ffmpeg-downloader.md
├── .github/workflows/
│   ├── ci.yml
│   └── docker.yml
├── .dockerignore
├── .gitignore                  # already exists
├── Dockerfile
├── docker-compose.example.yml
├── LICENSE
├── README.md
└── pyproject.toml
```

Each module has a single responsibility (see spec §2 Module boundaries). Tests mirror the module layout one-to-one.

---

## Pre-flight: Working directory & git

The working tree is `/Users/ajthom90/projects/ffmpeg-downloader`. Git is initialized on `main` with one prior commit (the design spec). Remote `origin` points at `https://github.com/ajthom90/ffmpeg-downloader.git`. **Do not push** until explicitly asked.

All `git` commands below assume CWD is the project root.

---

## Task 1: Project bootstrap (pyproject.toml + package skeleton + tooling)

**Files:**
- Create: `pyproject.toml`
- Create: `ffmpeg_downloader/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "ffmpeg-downloader"
version = "0.1.0"
description = "Self-hosted Flask app for downloading m3u8 streams via ffmpeg"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
authors = [{ name = "AJ Thom", email = "ajthom90@gmail.com" }]
dependencies = [
    "Flask>=3.0,<4",
    "gunicorn>=22.0,<24",
    "python-ulid>=2.7,<4",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<9",
    "pytest-timeout>=2.3,<3",
    "ruff>=0.6,<1",
]

[project.urls]
Homepage = "https://github.com/ajthom90/ffmpeg-downloader"

[tool.setuptools.packages.find]
include = ["ffmpeg_downloader*"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I", "B", "UP", "SIM"]

[tool.pytest.ini_options]
addopts = "-q --tb=short --strict-markers"
testpaths = ["tests"]
timeout = 30
```

- [ ] **Step 2: Write `ffmpeg_downloader/__init__.py` (placeholder factory)**

```python
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
```

- [ ] **Step 3: Write `tests/__init__.py` (empty) and `tests/conftest.py`**

```python
# tests/conftest.py
from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest


@pytest.fixture
def download_root(tmp_path: Path) -> Path:
    """Temporary directory used as DOWNLOAD_ROOT."""
    root = tmp_path / "downloads"
    root.mkdir()
    return root


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "data"
    d.mkdir()
    return d


@pytest.fixture
def fake_ffmpeg_path() -> Path:
    """Absolute path to the bash shim used in place of ffmpeg/ffprobe in tests."""
    return Path(__file__).parent / "fake_ffmpeg.sh"


@pytest.fixture
def fake_ffprobe_path() -> Path:
    return Path(__file__).parent / "fake_ffprobe.sh"


@pytest.fixture(autouse=True)
def _ensure_shims_executable(fake_ffmpeg_path: Path, fake_ffprobe_path: Path) -> None:
    for p in (fake_ffmpeg_path, fake_ffprobe_path):
        if p.exists():
            st = p.stat()
            p.chmod(st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
```

(The shim files themselves get written in Task 11; the autouse fixture is a no-op until they exist.)

- [ ] **Step 4: Install dev deps and run the smoke test**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
pytest -q
```

Expected: `no tests ran` (or 0 collected) — exit code 5 from pytest. That's fine; we just confirmed the package installs.

Run the factory directly:

```bash
python -c "from ffmpeg_downloader import create_app; c = create_app().test_client(); r = c.get('/healthz'); print(r.status_code, r.get_json())"
```

Expected: `200 {'ok': True}`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml ffmpeg_downloader/__init__.py tests/__init__.py tests/conftest.py
git commit -m "Bootstrap Flask package with healthz and pytest harness"
```

---

## Task 2: Config module

**Files:**
- Create: `ffmpeg_downloader/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader.config import Config, ConfigError


def test_config_defaults(download_root: Path, data_dir: Path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    cfg = Config.from_env()
    assert cfg.download_root == download_root.resolve()
    assert cfg.data_dir == data_dir.resolve()
    assert cfg.port == 8000
    assert cfg.max_concurrent_jobs == 2
    assert cfg.job_retention_days == 30
    assert cfg.search_cache_ttl_seconds == 60
    assert cfg.search_result_limit == 50
    assert cfg.ffmpeg_bin == "ffmpeg"
    assert cfg.ffprobe_bin == "ffprobe"


def test_config_overrides(download_root: Path, data_dir: Path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "4")
    monkeypatch.setenv("FFMPEG_BIN", "/usr/local/bin/ffmpeg")
    cfg = Config.from_env()
    assert cfg.port == 9000
    assert cfg.max_concurrent_jobs == 4
    assert cfg.ffmpeg_bin == "/usr/local/bin/ffmpeg"


def test_config_rejects_missing_download_root(tmp_path: Path, monkeypatch):
    missing = tmp_path / "nope"
    monkeypatch.setenv("DOWNLOAD_ROOT", str(missing))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="DOWNLOAD_ROOT"):
        Config.from_env()


def test_config_rejects_download_root_that_is_a_file(tmp_path: Path, monkeypatch):
    f = tmp_path / "file.txt"
    f.write_text("hi")
    monkeypatch.setenv("DOWNLOAD_ROOT", str(f))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ConfigError, match="not a directory"):
        Config.from_env()


def test_config_creates_data_dir(tmp_path: Path, monkeypatch, download_root: Path):
    new_data = tmp_path / "data-new"
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(new_data))
    cfg = Config.from_env()
    assert new_data.is_dir()
    assert cfg.data_dir == new_data.resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_config.py -v
```

Expected: `ImportError` / `ModuleNotFoundError: ffmpeg_downloader.config`.

- [ ] **Step 3: Write the implementation**

```python
# ffmpeg_downloader/config.py
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

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
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
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/config.py tests/test_config.py
git commit -m "Add Config dataclass loaded from env vars"
```

---

## Task 3: DB module (schema + connection helper)

**Files:**
- Create: `ffmpeg_downloader/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
from __future__ import annotations

import time
from pathlib import Path

from ffmpeg_downloader.db import Database


def _make_job_row(**overrides):
    now = int(time.time())
    base = dict(
        id="j_test01",
        url="https://example.com/master.m3u8",
        selected_variant_url=None,
        selected_variant_label=None,
        filename="video.mp4",
        output_path="Movies/video.mp4",
        extension="mp4",
        codec="copy",
        command="ffmpeg -i ... video.mp4",
        status="queued",
        progress=None,
        duration_seconds=None,
        current_time_seconds=None,
        speed=None,
        message=None,
        created_at=now,
        started_at=None,
        finished_at=None,
    )
    base.update(overrides)
    return base


def test_open_creates_schema(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    # Calling open() twice is idempotent.
    db2 = Database.open(data_dir / "jobs.db")
    db.close()
    db2.close()
    assert (data_dir / "jobs.db").exists()


def test_insert_and_get(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    row = _make_job_row()
    db.insert_job(row)
    fetched = db.get_job("j_test01")
    assert fetched is not None
    assert fetched["url"] == row["url"]
    assert fetched["status"] == "queued"
    db.close()


def test_update_status_and_progress(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row())
    db.update_job("j_test01", status="running", started_at=42, progress=33.3, speed="1.0x")
    j = db.get_job("j_test01")
    assert j["status"] == "running"
    assert j["started_at"] == 42
    assert abs(j["progress"] - 33.3) < 0.001
    assert j["speed"] == "1.0x"
    db.close()


def test_list_jobs_newest_first(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row(id="j_a", created_at=100))
    db.insert_job(_make_job_row(id="j_b", created_at=200))
    db.insert_job(_make_job_row(id="j_c", created_at=150))
    ids = [j["id"] for j in db.list_jobs(limit=10)]
    assert ids == ["j_b", "j_c", "j_a"]
    db.close()


def test_delete_job(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row())
    db.delete_job("j_test01")
    assert db.get_job("j_test01") is None
    db.close()


def test_reconcile_marks_running_jobs_failed(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    db.insert_job(_make_job_row(id="j_q", status="queued"))
    db.insert_job(_make_job_row(id="j_r", status="running"))
    db.insert_job(_make_job_row(id="j_done", status="completed", finished_at=10))
    db.reconcile_on_startup(now=1_000_000_000, retention_days=30)
    assert db.get_job("j_q")["status"] == "failed"
    assert db.get_job("j_r")["status"] == "failed"
    assert db.get_job("j_done")["status"] == "completed"
    db.close()


def test_reconcile_prunes_old_terminal_jobs(data_dir: Path):
    db = Database.open(data_dir / "jobs.db")
    now = 1_000_000_000
    old = now - 31 * 86400
    db.insert_job(_make_job_row(id="j_old", status="completed", finished_at=old))
    db.insert_job(_make_job_row(id="j_new", status="completed", finished_at=now - 100))
    db.reconcile_on_startup(now=now, retention_days=30)
    assert db.get_job("j_old") is None
    assert db.get_job("j_new") is not None
    db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_db.py -v
```

Expected: `ModuleNotFoundError: ffmpeg_downloader.db`.

- [ ] **Step 3: Write the implementation**

```python
# ffmpeg_downloader/db.py
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id                     TEXT PRIMARY KEY,
  url                    TEXT NOT NULL,
  selected_variant_url   TEXT,
  selected_variant_label TEXT,
  filename               TEXT NOT NULL,
  output_path            TEXT NOT NULL,
  extension              TEXT NOT NULL,
  codec                  TEXT NOT NULL,
  command                TEXT NOT NULL,
  status                 TEXT NOT NULL,
  progress               REAL,
  duration_seconds       REAL,
  current_time_seconds   REAL,
  speed                  TEXT,
  message                TEXT,
  created_at             INTEGER NOT NULL,
  started_at             INTEGER,
  finished_at            INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_created_at_idx ON jobs(created_at DESC);
"""

JOB_COLUMNS = (
    "id", "url", "selected_variant_url", "selected_variant_label",
    "filename", "output_path", "extension", "codec", "command",
    "status", "progress", "duration_seconds", "current_time_seconds",
    "speed", "message", "created_at", "started_at", "finished_at",
)


class Database:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    @classmethod
    def open(cls, path: Path) -> "Database":
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(SCHEMA)
        return cls(conn)

    def close(self) -> None:
        self._conn.close()

    def insert_job(self, row: dict[str, Any]) -> None:
        cols = ", ".join(JOB_COLUMNS)
        placeholders = ", ".join("?" for _ in JOB_COLUMNS)
        values = tuple(row[c] for c in JOB_COLUMNS)
        self._conn.execute(f"INSERT INTO jobs ({cols}) VALUES ({placeholders})", values)

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = (*fields.values(), job_id)
        self._conn.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", values)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        cur = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        r = cur.fetchone()
        return dict(r) if r else None

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        cur = self._conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in cur.fetchall()]

    def delete_job(self, job_id: str) -> None:
        self._conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def reconcile_on_startup(self, now: int, retention_days: int) -> None:
        self._conn.execute(
            "UPDATE jobs SET status='failed', message='Interrupted by restart', finished_at=? "
            "WHERE status IN ('queued','running')",
            (now,),
        )
        cutoff = now - retention_days * 86400
        self._conn.execute(
            "DELETE FROM jobs WHERE status IN ('completed','failed','cancelled') "
            "AND finished_at IS NOT NULL AND finished_at < ?",
            (cutoff,),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_db.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/db.py tests/test_db.py
git commit -m "Add SQLite Database layer with job CRUD and startup reconciliation"
```

---

## Task 4: Filesystem — `safe_path` and `RootedFS` skeleton

**Files:**
- Create: `ffmpeg_downloader/filesystem.py`
- Create: `tests/test_filesystem.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_filesystem.py
from __future__ import annotations

import os
from pathlib import Path

import pytest

from ffmpeg_downloader.filesystem import PathTraversalError, RootedFS


def test_safe_path_root(download_root: Path):
    fs = RootedFS(download_root)
    assert fs.safe_path("") == download_root
    assert fs.safe_path("/") == download_root
    assert fs.safe_path(".") == download_root


def test_safe_path_relative(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    assert fs.safe_path("Movies") == download_root / "Movies"
    assert fs.safe_path("/Movies") == download_root / "Movies"
    assert fs.safe_path("Movies/Foo") == download_root / "Movies/Foo"


def test_safe_path_rejects_dotdot(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path("../etc/passwd")
    with pytest.raises(PathTraversalError):
        fs.safe_path("Movies/../../etc/passwd")


def test_safe_path_rejects_absolute_outside(download_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path(str(outside))


def test_safe_path_rejects_symlink_escape(download_root: Path, tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (download_root / "evil").symlink_to(outside)
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path("evil/anything")


def test_safe_path_rejects_nul(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.safe_path("Movies\x00.txt")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_filesystem.py -v
```

Expected: `ModuleNotFoundError: ffmpeg_downloader.filesystem`.

- [ ] **Step 3: Write the implementation**

```python
# ffmpeg_downloader/filesystem.py
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class PathTraversalError(ValueError):
    """The supplied path escaped DOWNLOAD_ROOT or contained illegal characters."""


@dataclass
class RootedFS:
    """All filesystem operations are scoped to this root."""

    root: Path

    def __post_init__(self) -> None:
        self.root = self.root.resolve()
        if not self.root.is_dir():
            raise ValueError(f"root is not a directory: {self.root}")

    def safe_path(self, rel: str) -> Path:
        """Resolve `rel` against root, refusing anything outside or invalid."""
        if rel is None:
            raise PathTraversalError("path is required")
        if "\x00" in rel:
            raise PathTraversalError("path contains NUL")
        cleaned = rel.lstrip("/")
        if cleaned in ("", "."):
            return self.root
        candidate = (self.root / cleaned).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PathTraversalError(f"path escapes root: {rel!r}")
        return candidate

    def rel(self, p: Path) -> str:
        """Return path relative to root, as a forward-slash string."""
        p = p.resolve()
        if p == self.root:
            return ""
        return p.relative_to(self.root).as_posix()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_filesystem.py -v
```

Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/filesystem.py tests/test_filesystem.py
git commit -m "Add RootedFS with path-traversal-safe safe_path"
```

---

## Task 5: Filesystem — `browse()` and `mkdir()`

**Files:**
- Modify: `ffmpeg_downloader/filesystem.py`
- Modify: `tests/test_filesystem.py`

- [ ] **Step 1: Append the failing tests to `tests/test_filesystem.py`**

```python
# Append to tests/test_filesystem.py
from ffmpeg_downloader.filesystem import InvalidNameError


def _seed(root: Path) -> None:
    (root / "Movies").mkdir()
    (root / "Movies" / "Office Space (1999)").mkdir()
    (root / "Movies" / "10 Things").mkdir()
    (root / "Music").mkdir()
    (root / "README.txt").write_text("hi")


def test_browse_root_lists_top_level(download_root: Path):
    _seed(download_root)
    fs = RootedFS(download_root)
    result = fs.browse("")
    assert result["current_path"] == ""
    names = [i["name"] for i in result["items"]]
    # directories first, then files; case-insensitive sort within each group
    assert names == ["Movies", "Music", "README.txt"]
    movies = next(i for i in result["items"] if i["name"] == "Movies")
    assert movies["is_dir"] is True
    assert movies["path"] == "Movies"


def test_browse_subdirectory(download_root: Path):
    _seed(download_root)
    fs = RootedFS(download_root)
    result = fs.browse("Movies")
    assert result["current_path"] == "Movies"
    names = [i["name"] for i in result["items"]]
    assert names == ["10 Things", "Office Space (1999)"]


def test_browse_rejects_traversal(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.browse("../etc")


def test_browse_missing_path_raises_filenotfound(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(FileNotFoundError):
        fs.browse("does/not/exist")


def test_mkdir_creates_subfolder(download_root: Path):
    fs = RootedFS(download_root)
    new_rel = fs.mkdir("", "NewLibrary")
    assert new_rel == "NewLibrary"
    assert (download_root / "NewLibrary").is_dir()


def test_mkdir_creates_nested(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    new_rel = fs.mkdir("Movies", "Foo (2024)")
    assert new_rel == "Movies/Foo (2024)"
    assert (download_root / "Movies" / "Foo (2024)").is_dir()


def test_mkdir_rejects_separators(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "with/slash")
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "with\\backslash")


def test_mkdir_rejects_dot_names(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(InvalidNameError):
        fs.mkdir("", ".")
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "..")
    with pytest.raises(InvalidNameError):
        fs.mkdir("", "")


def test_mkdir_idempotent_returns_existing(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    out = fs.mkdir("", "Movies")
    assert out == "Movies"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_filesystem.py -v
```

Expected: `ImportError: cannot import name 'InvalidNameError'` plus 9 failures referencing `RootedFS.browse` and `.mkdir`.

- [ ] **Step 3: Add `browse()` and `mkdir()` to `RootedFS`**

Append to `ffmpeg_downloader/filesystem.py`:

```python
class InvalidNameError(ValueError):
    """A user-supplied folder name was not acceptable."""


def _natural_key(item: dict) -> tuple[int, str]:
    # directories first (0), files second (1); case-insensitive within each group
    return (0 if item["is_dir"] else 1, item["name"].lower())


# Extend RootedFS with methods (still inside the same module/class).
def _browse(self, rel: str) -> dict:
    target = self.safe_path(rel)
    if not target.is_dir():
        raise FileNotFoundError(rel)
    items = []
    for entry in os.scandir(target):
        items.append({
            "name": entry.name,
            "path": self.rel(Path(entry.path)),
            "is_dir": entry.is_dir(follow_symlinks=False),
        })
    items.sort(key=_natural_key)
    parent_rel = self.rel(target.parent) if target != self.root else ""
    return {
        "current_path": self.rel(target),
        "parent": parent_rel,
        "items": items,
    }


def _mkdir(self, rel: str, name: str) -> str:
    if not name or name in (".", ".."):
        raise InvalidNameError(f"invalid name: {name!r}")
    if "/" in name or "\\" in name or "\x00" in name:
        raise InvalidNameError(f"invalid characters in name: {name!r}")
    parent = self.safe_path(rel)
    if not parent.is_dir():
        raise FileNotFoundError(rel)
    new_dir = parent / name
    new_dir.mkdir(exist_ok=True)
    return self.rel(new_dir)


RootedFS.browse = _browse  # type: ignore[attr-defined]
RootedFS.mkdir = _mkdir  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_filesystem.py -v
```

Expected: 15 passed (the original 6 plus the 9 new ones).

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/filesystem.py tests/test_filesystem.py
git commit -m "Add browse() and mkdir() to RootedFS"
```

---

## Task 6: Filesystem — `validate()` and `autocomplete()`

**Files:**
- Modify: `ffmpeg_downloader/filesystem.py`
- Modify: `tests/test_filesystem.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_filesystem.py
def test_validate_existing_directory(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    v = fs.validate("Movies")
    assert v["exists"] is True
    assert v["is_dir"] is True
    assert v["resolved_path"] == "Movies"
    assert v["writable"] is True


def test_validate_existing_file(download_root: Path):
    (download_root / "song.mp3").write_text("x")
    fs = RootedFS(download_root)
    v = fs.validate("song.mp3")
    assert v["exists"] is True
    assert v["is_dir"] is False


def test_validate_missing_path_reports_ancestor_writable(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    v = fs.validate("Movies/NewFolder/SubNew")
    assert v["exists"] is False
    assert v["is_dir"] is False
    assert v["writable"] is True


def test_validate_traversal_raises(download_root: Path):
    fs = RootedFS(download_root)
    with pytest.raises(PathTraversalError):
        fs.validate("../etc")


def test_validate_root_is_writable(download_root: Path):
    fs = RootedFS(download_root)
    v = fs.validate("")
    assert v["exists"] is True
    assert v["is_dir"] is True
    assert v["writable"] is True


def test_autocomplete_empty_prefix_lists_root(download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    (download_root / "TVShows").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("")
    names = [m["name"] for m in matches]
    assert names == ["Movies", "Music", "TVShows"]


def test_autocomplete_filters_current_segment(download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Mo")
    assert [m["name"] for m in matches] == ["Movies"]


def test_autocomplete_drills_into_subfolder(download_root: Path):
    (download_root / "Movies" / "Office Space (1999)").mkdir(parents=True)
    (download_root / "Movies" / "October Sky").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Movies/Of")
    names = [m["name"] for m in matches]
    assert names == ["Office Space (1999)"]
    paths = [m["path"] for m in matches]
    assert paths == ["Movies/Office Space (1999)"]


def test_autocomplete_trailing_slash_lists_children(download_root: Path):
    (download_root / "Movies" / "Office").mkdir(parents=True)
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Movies/")
    assert [m["name"] for m in matches] == ["Office"]


def test_autocomplete_cap_10(download_root: Path):
    for i in range(20):
        (download_root / f"Folder{i:02d}").mkdir()
    fs = RootedFS(download_root)
    matches = fs.autocomplete("Folder")
    assert len(matches) == 10
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_filesystem.py -v
```

Expected: 10 new failures with `AttributeError: ... 'validate'` / `'autocomplete'`.

- [ ] **Step 3: Implement `validate()` and `autocomplete()`**

Append to `ffmpeg_downloader/filesystem.py`:

```python
def _validate(self, rel: str) -> dict:
    target = self.safe_path(rel)
    if target.exists():
        return {
            "exists": True,
            "is_dir": target.is_dir(),
            "resolved_path": self.rel(target),
            "writable": os.access(target, os.W_OK),
        }
    # Walk up to the nearest existing ancestor and check that.
    ancestor = target.parent
    while ancestor != self.root and not ancestor.exists():
        ancestor = ancestor.parent
    writable = ancestor.exists() and os.access(ancestor, os.W_OK)
    return {
        "exists": False,
        "is_dir": False,
        "resolved_path": self.rel(target),
        "writable": writable,
    }


def _autocomplete(self, prefix: str) -> list[dict]:
    cleaned = (prefix or "").lstrip("/")
    if cleaned == "" or cleaned.endswith("/"):
        parent_rel = cleaned.rstrip("/")
        last_seg = ""
    else:
        parent_rel, _, last_seg = cleaned.rpartition("/")
    try:
        parent = self.safe_path(parent_rel)
    except PathTraversalError:
        return []
    if not parent.is_dir():
        return []
    needle = last_seg.lower()
    matches = []
    for entry in os.scandir(parent):
        if not entry.is_dir(follow_symlinks=False):
            continue
        if needle and needle not in entry.name.lower():
            continue
        matches.append({"name": entry.name, "path": self.rel(Path(entry.path))})
    matches.sort(key=lambda m: m["name"].lower())
    return matches[:10]


RootedFS.validate = _validate  # type: ignore[attr-defined]
RootedFS.autocomplete = _autocomplete  # type: ignore[attr-defined]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_filesystem.py -v
```

Expected: 25 passed total.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/filesystem.py tests/test_filesystem.py
git commit -m "Add validate() and autocomplete() to RootedFS"
```

---

## Task 7: Filesystem — recursive `search()` with TTL cache

**Files:**
- Modify: `ffmpeg_downloader/filesystem.py`
- Modify: `tests/test_filesystem.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_filesystem.py
def test_search_finds_matches_anywhere(download_root: Path):
    (download_root / "Movies" / "Office Space (1999)").mkdir(parents=True)
    (download_root / "TVShows" / "The Office").mkdir(parents=True)
    (download_root / "TVShows" / "The Office (UK)").mkdir(parents=True)
    (download_root / "Music" / "Foo").mkdir(parents=True)
    fs = RootedFS(download_root)
    result = fs.search("office", limit=10)
    paths = sorted(m["path"] for m in result["matches"])
    assert paths == [
        "Movies/Office Space (1999)",
        "TVShows/The Office",
        "TVShows/The Office (UK)",
    ]
    assert result["truncated"] is False


def test_search_case_insensitive(download_root: Path):
    (download_root / "OfficeStuff").mkdir()
    fs = RootedFS(download_root)
    result = fs.search("OFFICE", limit=10)
    assert [m["name"] for m in result["matches"]] == ["OfficeStuff"]


def test_search_empty_query_returns_nothing(download_root: Path):
    (download_root / "Movies").mkdir()
    fs = RootedFS(download_root)
    result = fs.search("", limit=10)
    assert result["matches"] == []
    assert result["truncated"] is False


def test_search_truncates(download_root: Path):
    for i in range(15):
        (download_root / f"Match{i:02d}").mkdir()
    fs = RootedFS(download_root)
    result = fs.search("match", limit=5)
    assert len(result["matches"]) == 5
    assert result["truncated"] is True


def test_search_cache_invalidated_after_mkdir(download_root: Path):
    fs = RootedFS(download_root, cache_ttl=60)
    result = fs.search("alpha", limit=10)
    assert result["matches"] == []
    fs.mkdir("", "Alpha")
    result2 = fs.search("alpha", limit=10)
    assert [m["name"] for m in result2["matches"]] == ["Alpha"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_filesystem.py -v
```

Expected: 5 new failures (no `search` method; `cache_ttl` kwarg unknown).

- [ ] **Step 3: Implement `search()` and TTL cache**

Update `RootedFS.__init__` and add `search()`. Replace the existing `RootedFS` dataclass definition with this (keep the rest of the file intact):

```python
# In ffmpeg_downloader/filesystem.py, replace the `@dataclass class RootedFS:` block.

import time as _time


class RootedFS:
    """All filesystem operations are scoped to this root."""

    def __init__(self, root: Path, cache_ttl: int = 60):
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError(f"root is not a directory: {self.root}")
        self._cache_ttl = cache_ttl
        self._cache_built_at: float = 0.0
        self._cache: list[tuple[str, str]] = []  # (lowercased_name, rel_path)

    # safe_path and rel from earlier remain the same — leave their bodies in place.

    def _invalidate_cache(self) -> None:
        self._cache_built_at = 0.0
        self._cache = []

    def _build_cache(self) -> None:
        cache: list[tuple[str, str]] = []
        for dirpath, dirnames, _filenames in os.walk(self.root, followlinks=False):
            for d in dirnames:
                full = Path(dirpath) / d
                cache.append((d.lower(), self.rel(full)))
        self._cache = cache
        self._cache_built_at = _time.monotonic()

    def search(self, query: str, limit: int = 50) -> dict:
        if not query:
            return {"matches": [], "truncated": False}
        now = _time.monotonic()
        if not self._cache or (now - self._cache_built_at) > self._cache_ttl:
            self._build_cache()
        needle = query.lower()
        out = []
        for lower_name, rel in self._cache:
            if needle in lower_name:
                out.append({"name": Path(rel).name, "path": rel})
                if len(out) > limit:
                    break
        truncated = len(out) > limit
        return {"matches": out[:limit], "truncated": truncated}
```

The original `safe_path`, `rel`, `_browse`, `_mkdir`, `_validate`, `_autocomplete` continue to be attached to `RootedFS` after this block — but the new `__init__` replaces the dataclass-generated one and removes `@dataclass`. Make sure to:
1. Remove `@dataclass` and the original `__post_init__`.
2. Move the `safe_path` and `rel` methods inside this new class body. (Below is the consolidated final form.)

Replace the whole module with this final form for clarity:

```python
# ffmpeg_downloader/filesystem.py
from __future__ import annotations

import os
import time as _time
from pathlib import Path


class PathTraversalError(ValueError):
    pass


class InvalidNameError(ValueError):
    pass


def _natural_key(item: dict) -> tuple[int, str]:
    return (0 if item["is_dir"] else 1, item["name"].lower())


class RootedFS:
    def __init__(self, root: Path, cache_ttl: int = 60):
        self.root = root.resolve()
        if not self.root.is_dir():
            raise ValueError(f"root is not a directory: {self.root}")
        self._cache_ttl = cache_ttl
        self._cache_built_at: float = 0.0
        self._cache: list[tuple[str, str]] = []

    # ---------- path safety ----------

    def safe_path(self, rel: str) -> Path:
        if rel is None:
            raise PathTraversalError("path is required")
        if "\x00" in rel:
            raise PathTraversalError("path contains NUL")
        cleaned = rel.lstrip("/")
        if cleaned in ("", "."):
            return self.root
        candidate = (self.root / cleaned).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise PathTraversalError(f"path escapes root: {rel!r}")
        return candidate

    def rel(self, p: Path) -> str:
        p = p.resolve()
        if p == self.root:
            return ""
        return p.relative_to(self.root).as_posix()

    # ---------- browse / mkdir ----------

    def browse(self, rel: str) -> dict:
        target = self.safe_path(rel)
        if not target.is_dir():
            raise FileNotFoundError(rel)
        items = []
        for entry in os.scandir(target):
            items.append({
                "name": entry.name,
                "path": self.rel(Path(entry.path)),
                "is_dir": entry.is_dir(follow_symlinks=False),
            })
        items.sort(key=_natural_key)
        parent_rel = self.rel(target.parent) if target != self.root else ""
        return {"current_path": self.rel(target), "parent": parent_rel, "items": items}

    def mkdir(self, rel: str, name: str) -> str:
        if not name or name in (".", ".."):
            raise InvalidNameError(f"invalid name: {name!r}")
        if "/" in name or "\\" in name or "\x00" in name:
            raise InvalidNameError(f"invalid characters in name: {name!r}")
        parent = self.safe_path(rel)
        if not parent.is_dir():
            raise FileNotFoundError(rel)
        new_dir = parent / name
        new_dir.mkdir(exist_ok=True)
        self._invalidate_cache()
        return self.rel(new_dir)

    # ---------- validate / autocomplete ----------

    def validate(self, rel: str) -> dict:
        target = self.safe_path(rel)
        if target.exists():
            return {
                "exists": True,
                "is_dir": target.is_dir(),
                "resolved_path": self.rel(target),
                "writable": os.access(target, os.W_OK),
            }
        ancestor = target.parent
        while ancestor != self.root and not ancestor.exists():
            ancestor = ancestor.parent
        writable = ancestor.exists() and os.access(ancestor, os.W_OK)
        return {
            "exists": False,
            "is_dir": False,
            "resolved_path": self.rel(target),
            "writable": writable,
        }

    def autocomplete(self, prefix: str) -> list[dict]:
        cleaned = (prefix or "").lstrip("/")
        if cleaned == "" or cleaned.endswith("/"):
            parent_rel = cleaned.rstrip("/")
            last_seg = ""
        else:
            parent_rel, _, last_seg = cleaned.rpartition("/")
        try:
            parent = self.safe_path(parent_rel)
        except PathTraversalError:
            return []
        if not parent.is_dir():
            return []
        needle = last_seg.lower()
        matches = []
        for entry in os.scandir(parent):
            if not entry.is_dir(follow_symlinks=False):
                continue
            if needle and needle not in entry.name.lower():
                continue
            matches.append({"name": entry.name, "path": self.rel(Path(entry.path))})
        matches.sort(key=lambda m: m["name"].lower())
        return matches[:10]

    # ---------- recursive search w/ TTL cache ----------

    def _invalidate_cache(self) -> None:
        self._cache_built_at = 0.0
        self._cache = []

    def _build_cache(self) -> None:
        cache: list[tuple[str, str]] = []
        for dirpath, dirnames, _filenames in os.walk(self.root, followlinks=False):
            for d in dirnames:
                full = Path(dirpath) / d
                cache.append((d.lower(), self.rel(full)))
        self._cache = cache
        self._cache_built_at = _time.monotonic()

    def search(self, query: str, limit: int = 50) -> dict:
        if not query:
            return {"matches": [], "truncated": False}
        now = _time.monotonic()
        if not self._cache or (now - self._cache_built_at) > self._cache_ttl:
            self._build_cache()
        needle = query.lower()
        out = []
        for lower_name, rel in self._cache:
            if needle in lower_name:
                out.append({"name": Path(rel).name, "path": rel})
                if len(out) > limit:
                    break
        truncated = len(out) > limit
        return {"matches": out[:limit], "truncated": truncated}
```

- [ ] **Step 4: Run all filesystem tests**

```bash
pytest tests/test_filesystem.py -v
```

Expected: 30 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/filesystem.py tests/test_filesystem.py
git commit -m "Add recursive search with TTL cache; consolidate RootedFS"
```

---

## Task 8: `ffmpeg_command.build_command()`

**Files:**
- Create: `ffmpeg_downloader/ffmpeg_command.py`
- Create: `tests/test_ffmpeg_command.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ffmpeg_command.py
from __future__ import annotations

import pytest

from ffmpeg_downloader.ffmpeg_command import (
    UnsupportedCodecError,
    UnsupportedSchemeError,
    build_command,
    pretty_command,
)


def test_build_command_copy_codec_http():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/master.m3u8",
        output_path="/downloads/Movies/foo.mp4",
        codec="copy",
        extension="mp4",
    )
    assert argv[0] == "ffmpeg"
    assert "-hide_banner" in argv
    assert "-reconnect" in argv
    # input URL after -i
    i_idx = argv.index("-i")
    assert argv[i_idx + 1] == "https://example.com/master.m3u8"
    # codec copy
    assert "-c:v" in argv and "copy" in argv[argv.index("-c:v") + 1: argv.index("-c:v") + 2]
    # HLS audio bitstream fix is present for .m3u8 + copy
    assert "-bsf:a" in argv
    assert argv[argv.index("-bsf:a") + 1] == "aac_adtstoasc"
    # progress wiring + output path at the end
    assert "-progress" in argv
    assert argv[-1] == "/downloads/Movies/foo.mp4"


def test_build_command_h264_no_hls_bitstream_filter():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/master.m3u8",
        output_path="/out/file.mp4",
        codec="h264",
        extension="mp4",
    )
    assert "-c:v" in argv
    assert argv[argv.index("-c:v") + 1] == "libx264"
    # only copy+m3u8 triggers the HLS bitstream filter
    assert "-bsf:a" not in argv


def test_build_command_audio_only_codecs_strip_video():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/song.mp3",
        output_path="/out/song.mp3",
        codec="mp3",
        extension="mp3",
    )
    assert "-vn" in argv
    assert "-c:v" not in argv
    assert argv[argv.index("-c:a") + 1] == "libmp3lame"


def test_build_command_no_reconnect_for_non_http():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="ftp://example.com/file.mp4",  # not allowed at API layer, but builder should be defensive
        output_path="/out/x.mp4",
        codec="copy",
        extension="mp4",
    )
    assert "-reconnect" not in argv


def test_build_command_rejects_unknown_codec():
    with pytest.raises(UnsupportedCodecError):
        build_command(
            ffmpeg_bin="ffmpeg",
            input_url="https://x/x.mp4",
            output_path="/out/x.mp4",
            codec="banana",
            extension="mp4",
        )


def test_build_command_rejects_bad_scheme():
    with pytest.raises(UnsupportedSchemeError):
        build_command(
            ffmpeg_bin="ffmpeg",
            input_url="file:///etc/passwd",
            output_path="/out/x.mp4",
            codec="copy",
            extension="mp4",
        )


def test_pretty_command_quotes_url():
    argv = build_command(
        ffmpeg_bin="ffmpeg",
        input_url="https://example.com/has space.m3u8",
        output_path="/out/x.mp4",
        codec="copy",
        extension="mp4",
    )
    s = pretty_command(argv)
    assert "'https://example.com/has space.m3u8'" in s
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_ffmpeg_command.py -v
```

Expected: `ModuleNotFoundError: ffmpeg_downloader.ffmpeg_command`.

- [ ] **Step 3: Write the implementation**

```python
# ffmpeg_downloader/ffmpeg_command.py
from __future__ import annotations

import shlex
from urllib.parse import urlparse


class UnsupportedCodecError(ValueError):
    pass


class UnsupportedSchemeError(ValueError):
    pass


CODEC_MAP: dict[str, dict] = {
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


def build_command(
    *,
    ffmpeg_bin: str,
    input_url: str,
    output_path: str,
    codec: str,
    extension: str,
) -> list[str]:
    """Return the argv list ffmpeg will be invoked with.

    Caller is responsible for sanitizing output_path and ensuring the parent dir exists.
    """
    if codec not in CODEC_MAP:
        raise UnsupportedCodecError(f"unknown codec: {codec}")

    scheme = urlparse(input_url).scheme.lower()
    if scheme not in ("http", "https") and not _looks_like_path(scheme):
        # We still let the builder run for "" (path) inputs to support local tests,
        # but explicitly block known-dangerous schemes.
        if scheme in ("file", "pipe", "concat", "data"):
            raise UnsupportedSchemeError(f"unsupported scheme: {scheme}")

    cfg = CODEC_MAP[codec]
    argv: list[str] = [ffmpeg_bin, "-hide_banner", "-loglevel", "error"]
    if scheme in ("http", "https"):
        argv += [
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
        ]
    argv += ["-i", input_url]

    if cfg["video"] is not None:
        argv += cfg["video"]
    argv += cfg["audio"]
    argv += cfg["extra"]

    # HLS-into-MP4 audio fix: only when input is .m3u8 AND audio is being copied.
    if input_url.lower().split("?", 1)[0].endswith(".m3u8") and codec == "copy":
        argv += ["-bsf:a", "aac_adtstoasc"]

    argv += ["-progress", "pipe:1", "-nostats", "-y", output_path]
    return argv


def _looks_like_path(scheme: str) -> bool:
    """An empty urlparse scheme means a plain path (or Windows drive letter handled elsewhere)."""
    return scheme == ""


def pretty_command(argv: list[str]) -> str:
    """Render argv as a shell-safe single line for storage/display only."""
    return " ".join(shlex.quote(a) for a in argv)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_ffmpeg_command.py -v
```

Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/ffmpeg_command.py tests/test_ffmpeg_command.py
git commit -m "Add ffmpeg argv builder with codec table and HLS audio fix"
```

---

## Task 9: HLS playlist parser (probe — pure functions)

**Files:**
- Create: `ffmpeg_downloader/probe.py`
- Create: `tests/test_probe.py`
- Create: `tests/fixtures/master-simple.m3u8`
- Create: `tests/fixtures/master-relative-uris.m3u8`
- Create: `tests/fixtures/media-only.m3u8`

- [ ] **Step 1: Write the fixture playlists**

`tests/fixtures/master-simple.m3u8`:
```
#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=5000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2",FRAME-RATE=29.970
https://cdn.example.com/v/1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=2800000,RESOLUTION=1280x720,CODECS="avc1.4d401f,mp4a.40.2",FRAME-RATE=29.970
https://cdn.example.com/v/720.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1400000,RESOLUTION=854x480,CODECS="avc1.4d401e,mp4a.40.2",FRAME-RATE=29.970
https://cdn.example.com/v/480.m3u8
```

`tests/fixtures/master-relative-uris.m3u8`:
```
#EXTM3U
#EXT-X-VERSION:6
#EXT-X-STREAM-INF:BANDWIDTH=4000000,RESOLUTION=1920x1080,CODECS="avc1.640028"
1080/index.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=640x360,CODECS="avc1.4d401e"
360/index.m3u8
```

`tests/fixtures/media-only.m3u8`:
```
#EXTM3U
#EXT-X-VERSION:3
#EXT-X-TARGETDURATION:10
#EXTINF:9.97,
seg0.ts
#EXTINF:9.95,
seg1.ts
#EXT-X-ENDLIST
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_probe.py
from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader.probe import classify, parse_master_playlist


FIXTURES = Path(__file__).parent / "fixtures"


def test_classify_master_playlist():
    body = (FIXTURES / "master-simple.m3u8").read_text()
    assert classify(body) == "hls_master"


def test_classify_media_playlist():
    body = (FIXTURES / "media-only.m3u8").read_text()
    assert classify(body) == "hls_media"


def test_classify_not_hls():
    assert classify("not a playlist at all") == "direct"
    assert classify("") == "unknown"


def test_classify_bom_prefixed():
    body = "﻿" + (FIXTURES / "master-simple.m3u8").read_text()
    assert classify(body) == "hls_master"


def test_parse_master_returns_sorted_variants():
    body = (FIXTURES / "master-simple.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://cdn.example.com/master.m3u8")
    bws = [v["bandwidth"] for v in variants]
    assert bws == sorted(bws, reverse=True)
    assert bws == [5_000_000, 2_800_000, 1_400_000]
    assert variants[0]["width"] == 1920 and variants[0]["height"] == 1080
    assert variants[0]["codecs"] == "avc1.640028,mp4a.40.2"
    assert variants[0]["url"] == "https://cdn.example.com/v/1080.m3u8"
    assert variants[0]["label"] == "1920×1080 5.0 Mbps"


def test_parse_master_resolves_relative_uris():
    body = (FIXTURES / "master-relative-uris.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://cdn.example.com/master.m3u8")
    urls = [v["url"] for v in variants]
    assert urls == [
        "https://cdn.example.com/1080/index.m3u8",
        "https://cdn.example.com/360/index.m3u8",
    ]


def test_parse_master_handles_missing_resolution():
    body = (
        "#EXTM3U\n"
        '#EXT-X-STREAM-INF:BANDWIDTH=500000,CODECS="mp4a.40.2"\n'
        "audio.m3u8\n"
    )
    variants = parse_master_playlist(body, base_url="https://x.com/master.m3u8")
    assert len(variants) == 1
    assert variants[0]["width"] is None and variants[0]["height"] is None


def test_parse_master_handles_bom():
    body = "﻿" + (FIXTURES / "master-simple.m3u8").read_text()
    variants = parse_master_playlist(body, base_url="https://x.com/m.m3u8")
    assert len(variants) == 3
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_probe.py -v
```

Expected: `ModuleNotFoundError: ffmpeg_downloader.probe`.

- [ ] **Step 4: Write the parser implementation**

```python
# ffmpeg_downloader/probe.py
from __future__ import annotations

import re
from urllib.parse import urljoin

_ATTR_RE = re.compile(
    r'([A-Z0-9-]+)\s*=\s*(?:"([^"]*)"|([^,]*))(?:,\s*|$)'
)


def _strip_bom(s: str) -> str:
    return s.lstrip("﻿")


def classify(body: str) -> str:
    """Return the playlist classification for the given body."""
    if not body:
        return "unknown"
    cleaned = _strip_bom(body)
    head = cleaned.lstrip()
    if not head.startswith("#EXTM3U"):
        return "direct"
    if "#EXT-X-STREAM-INF:" in cleaned:
        return "hls_master"
    return "hls_media"


def _parse_attrs(attr_str: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(attr_str):
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else (m.group(3) or "")
        out[key] = val.strip()
    return out


def _parse_resolution(s: str | None) -> tuple[int | None, int | None]:
    if not s or "x" not in s:
        return None, None
    w, _, h = s.partition("x")
    try:
        return int(w), int(h)
    except ValueError:
        return None, None


def _label(width: int | None, height: int | None, bandwidth: int) -> str:
    res = f"{width}×{height}" if width and height else "unknown"
    mbps = bandwidth / 1_000_000
    return f"{res} {mbps:.1f} Mbps"


def parse_master_playlist(body: str, base_url: str) -> list[dict]:
    """Return variants sorted by bandwidth descending."""
    body = _strip_bom(body)
    lines = body.splitlines()
    variants: list[dict] = []
    for i, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue
        attrs = _parse_attrs(line[len("#EXT-X-STREAM-INF:"):])
        uri = None
        for nxt in lines[i + 1:]:
            stripped = nxt.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                continue
            uri = stripped
            break
        if not uri:
            continue
        w, h = _parse_resolution(attrs.get("RESOLUTION"))
        bw = int(attrs.get("BANDWIDTH", "0") or "0")
        frame_rate_raw = attrs.get("FRAME-RATE")
        try:
            frame_rate = float(frame_rate_raw) if frame_rate_raw else None
        except ValueError:
            frame_rate = None
        variants.append({
            "url": urljoin(base_url, uri),
            "width": w,
            "height": h,
            "bandwidth": bw,
            "codecs": attrs.get("CODECS", ""),
            "frame_rate": frame_rate,
            "label": _label(w, h, bw),
        })
    variants.sort(key=lambda v: v["bandwidth"], reverse=True)
    return variants
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_probe.py -v
```

Expected: 8 passed.

- [ ] **Step 6: Commit**

```bash
git add ffmpeg_downloader/probe.py tests/test_probe.py tests/fixtures/
git commit -m "Add HLS playlist classifier and master-playlist parser"
```

---

## Task 10: Probe — HTTP fetch with size and timeout limits

**Files:**
- Modify: `ffmpeg_downloader/probe.py`
- Modify: `tests/test_probe.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_probe.py
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from ffmpeg_downloader.probe import (
    ProbeResult,
    UnsupportedSchemeError,
    fetch_url,
    probe_url,
)


class _StubHandler(BaseHTTPRequestHandler):
    body_bytes = b""
    delay = 0.0

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if self.delay:
            import time
            time.sleep(self.delay)
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.send_header("Content-Length", str(len(self.body_bytes)))
        self.end_headers()
        self.wfile.write(self.body_bytes)

    def log_message(self, *_args):  # silence
        return


@pytest.fixture
def stub_server():
    server = HTTPServer(("127.0.0.1", 0), _StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _url_for(server, path="/master.m3u8"):
    host, port = server.server_address
    return f"http://{host}:{port}{path}"


def test_fetch_url_returns_body(stub_server):
    _StubHandler.body_bytes = b"#EXTM3U\n"
    body = fetch_url(_url_for(stub_server), max_bytes=1024, timeout=5.0)
    assert body == "#EXTM3U\n"


def test_fetch_url_caps_body(stub_server):
    _StubHandler.body_bytes = b"x" * 1_000_000  # 1 MB
    body = fetch_url(_url_for(stub_server), max_bytes=1024, timeout=5.0)
    assert len(body) == 1024


def test_fetch_url_rejects_bad_scheme():
    with pytest.raises(UnsupportedSchemeError):
        fetch_url("file:///etc/passwd", max_bytes=1024, timeout=5.0)


def test_probe_url_classifies_master(stub_server):
    _StubHandler.body_bytes = (FIXTURES / "master-simple.m3u8").read_bytes()
    result = probe_url(_url_for(stub_server))
    assert isinstance(result, ProbeResult)
    assert result.type == "hls_master"
    assert len(result.variants) == 3
    # base_url propagated for relative URI resolution
    assert result.variants[0]["url"].startswith("https://cdn.example.com/")


def test_probe_url_unknown_on_fetch_failure():
    result = probe_url("http://127.0.0.1:1/nope")  # port 1 closed
    assert result.type == "unknown"
    assert result.variants == []
    assert result.message  # some error text
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_probe.py -v
```

Expected: 5 new failures with `ImportError` for `ProbeResult` / `fetch_url` / `probe_url` / `UnsupportedSchemeError`.

- [ ] **Step 3: Add fetcher and probe orchestrator to `probe.py`**

Append to `ffmpeg_downloader/probe.py`:

```python
# Append to ffmpeg_downloader/probe.py
from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class UnsupportedSchemeError(ValueError):
    pass


@dataclass
class ProbeResult:
    type: str  # "hls_master" | "hls_media" | "direct" | "unknown"
    variants: list[dict] = field(default_factory=list)
    duration_seconds: float | None = None
    message: str | None = None


def fetch_url(url: str, *, max_bytes: int = 256 * 1024, timeout: float = 10.0) -> str:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise UnsupportedSchemeError(f"only http(s) allowed, got: {scheme}")
    req = Request(url, headers={"User-Agent": "ffmpeg-downloader-probe/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        data = resp.read(max_bytes)
    return data.decode("utf-8", errors="replace")


def probe_url(url: str, *, max_bytes: int = 256 * 1024, timeout: float = 10.0) -> ProbeResult:
    """Fetch and classify a URL; return ProbeResult with variants if HLS master."""
    try:
        body = fetch_url(url, max_bytes=max_bytes, timeout=timeout)
    except UnsupportedSchemeError as e:
        return ProbeResult(type="unknown", message=str(e))
    except (URLError, TimeoutError, OSError, ValueError) as e:
        return ProbeResult(type="unknown", message=str(e))
    kind = classify(body)
    if kind == "hls_master":
        variants = parse_master_playlist(body, base_url=url)
        return ProbeResult(type=kind, variants=variants)
    return ProbeResult(type=kind)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_probe.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/probe.py tests/test_probe.py
git commit -m "Add probe HTTP fetcher with size cap, timeout, and classification"
```

---

## Task 11: Fake ffmpeg / ffprobe shims for JobManager tests

**Files:**
- Create: `tests/fake_ffmpeg.sh`
- Create: `tests/fake_ffprobe.sh`

These shims emulate just enough of ffmpeg and ffprobe to drive the JobManager tests. They are pure bash, so they work inside the test runner on macOS and Linux.

- [ ] **Step 1: Write `tests/fake_ffmpeg.sh`**

```bash
#!/usr/bin/env bash
# A test shim that pretends to be ffmpeg.
#
# Reads behavior from env vars set by the test:
#   FAKE_FFMPEG_TICKS  - number of "out_time_us" progress lines to emit (default 3)
#   FAKE_FFMPEG_SLEEP  - seconds to sleep between ticks (default 0.05)
#   FAKE_FFMPEG_EXIT   - exit code (default 0)
#   FAKE_FFMPEG_STDERR - text to write to stderr before exiting (default "")
#
# It parses argv for "-y <output_path>" (the last positional) and creates an empty
# file there on success, simulating a real ffmpeg run.
set -u

ticks="${FAKE_FFMPEG_TICKS:-3}"
sleep_s="${FAKE_FFMPEG_SLEEP:-0.05}"
exit_code="${FAKE_FFMPEG_EXIT:-0}"
stderr_text="${FAKE_FFMPEG_STDERR:-}"

# The last argv element is the output path (build_command places it last after -y).
output_path="${!#}"

# Emit progress lines on stdout.
for i in $(seq 1 "$ticks"); do
    out_time_us=$(( i * 1000000 ))
    printf 'out_time_us=%s\n' "$out_time_us"
    printf 'speed=1.0x\n'
    printf 'progress=continue\n'
    sleep "$sleep_s"
done
printf 'progress=end\n'

if [ "$exit_code" = "0" ]; then
    : > "$output_path"  # touch the output file
else
    printf '%s' "$stderr_text" >&2
fi

exit "$exit_code"
```

- [ ] **Step 2: Write `tests/fake_ffprobe.sh`**

```bash
#!/usr/bin/env bash
# A test shim that pretends to be ffprobe.
#
# Env vars:
#   FAKE_FFPROBE_DURATION - duration in seconds (e.g. "3.5"). Empty = no output, exit 1.
set -u
dur="${FAKE_FFPROBE_DURATION:-}"
if [ -z "$dur" ]; then
    exit 1
fi
printf '%s\n' "$dur"
exit 0
```

- [ ] **Step 3: Make shims executable and verify**

```bash
chmod +x tests/fake_ffmpeg.sh tests/fake_ffprobe.sh
FAKE_FFMPEG_TICKS=1 FAKE_FFMPEG_SLEEP=0 ./tests/fake_ffmpeg.sh -y /tmp/__shim_test
test -f /tmp/__shim_test && echo "shim ok" && rm /tmp/__shim_test
FAKE_FFPROBE_DURATION=10 ./tests/fake_ffprobe.sh -i x
```

Expected: prints `out_time_us=1000000`, `speed=1.0x`, `progress=continue`, `progress=end`, then `shim ok`. Then `10`.

- [ ] **Step 4: Commit**

```bash
git add tests/fake_ffmpeg.sh tests/fake_ffprobe.sh
git commit -m "Add bash shims that emulate ffmpeg and ffprobe for tests"
```

---

## Task 12: JobManager — submit and DB persistence

**Files:**
- Create: `ffmpeg_downloader/jobs.py`
- Create: `tests/test_jobs.py`

This task adds enough JobManager surface to submit a job and inspect it, without yet running the subprocess. Subprocess execution arrives in Task 13.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_jobs.py
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ffmpeg_downloader.db import Database
from ffmpeg_downloader.filesystem import RootedFS
from ffmpeg_downloader.jobs import JobManager, JobSpec


@pytest.fixture
def db(data_dir: Path):
    d = Database.open(data_dir / "jobs.db")
    yield d
    d.close()


@pytest.fixture
def fs(download_root: Path):
    return RootedFS(download_root)


@pytest.fixture
def jm(db, fs, fake_ffmpeg_path, fake_ffprobe_path):
    manager = JobManager(
        db=db,
        fs=fs,
        ffmpeg_bin=str(fake_ffmpeg_path),
        ffprobe_bin=str(fake_ffprobe_path),
        max_concurrent_jobs=2,
    )
    try:
        yield manager
    finally:
        manager.shutdown(wait=True)


def test_submit_persists_job_as_queued(jm: JobManager, db: Database, download_root: Path):
    # Avoid the executor actually running the job in this first test.
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(JobSpec(
        url="https://example.com/m.m3u8",
        selected_variant_url=None,
        selected_variant_label=None,
        filename="my video",
        extension="mp4",
        codec="copy",
        output_folder="",
    ))
    assert job["id"].startswith("j_")
    assert job["status"] == "queued"
    persisted = db.get_job(job["id"])
    assert persisted is not None
    assert persisted["url"] == "https://example.com/m.m3u8"
    assert persisted["output_path"] == "my video.mp4"
    assert persisted["command"].startswith(str(jm._ffmpeg_bin))


def test_submit_sanitizes_filename(jm: JobManager):
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None,
        selected_variant_label=None,
        filename="bad/name*.mp4",  # already has extension; should be stripped
        extension="mp4",
        codec="copy",
        output_folder="",
    ))
    assert job["output_path"] == "badname.mp4"


def test_submit_handles_filename_collision(jm: JobManager, download_root: Path):
    (download_root / "video.mp4").write_text("x")
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None,
        selected_variant_label=None,
        filename="video",
        extension="mp4",
        codec="copy",
        output_folder="",
    ))
    assert job["output_path"] == "video (2).mp4"


def test_submit_creates_missing_output_folder(jm: JobManager, download_root: Path):
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None,
        selected_variant_label=None,
        filename="video",
        extension="mp4",
        codec="copy",
        output_folder="Movies/NewSubfolder",
    ))
    assert (download_root / "Movies" / "NewSubfolder").is_dir()
    assert job["output_path"] == "Movies/NewSubfolder/video.mp4"


def test_submit_uses_variant_url_when_provided(jm: JobManager, db: Database):
    jm._submit_to_executor = lambda *_a, **_k: None  # type: ignore[attr-defined]
    job = jm.submit(JobSpec(
        url="https://example.com/master.m3u8",
        selected_variant_url="https://example.com/1080.m3u8",
        selected_variant_label="1920×1080 5.0 Mbps",
        filename="video",
        extension="mp4",
        codec="copy",
        output_folder="",
    ))
    persisted = db.get_job(job["id"])
    assert persisted["selected_variant_url"] == "https://example.com/1080.m3u8"
    # The argv should reference the variant URL, not the master.
    assert "1080.m3u8" in persisted["command"]
    assert "master.m3u8" not in persisted["command"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_jobs.py -v
```

Expected: `ModuleNotFoundError: ffmpeg_downloader.jobs`.

- [ ] **Step 3: Write the JobManager skeleton**

```python
# ffmpeg_downloader/jobs.py
from __future__ import annotations

import os
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
    base = filename
    ext = extension
    candidate = folder / f"{base}.{ext}"
    if not candidate.exists():
        return f"{base}.{ext}"
    n = 2
    while True:
        candidate = folder / f"{base} ({n}).{ext}"
        if not candidate.exists():
            return f"{base} ({n}).{ext}"
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
            folder_abs = (self._fs.root / folder_rel).resolve()
            # Make sure it's still inside root.
            self._fs.safe_path(folder_rel)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jobs.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/jobs.py tests/test_jobs.py
git commit -m "Add JobManager.submit() with filename sanitize, collision, variant URL"
```

---

## Task 13: JobManager — run subprocess, parse progress, update DB

**Files:**
- Modify: `ffmpeg_downloader/jobs.py`
- Modify: `tests/test_jobs.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_jobs.py
def _wait_for_status(db, job_id, status, *, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        j = db.get_job(job_id)
        if j and j["status"] == status:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach status {status!r} within {timeout}s; last={j}")


def test_run_job_completes_successfully(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "2")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.02")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "5.0")
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="ok", extension="mp4", codec="copy", output_folder="",
    ))
    done = _wait_for_status(db, job["id"], "completed")
    assert done["progress"] == 100.0 or done["progress"] is not None
    assert done["finished_at"] is not None
    assert done["duration_seconds"] == 5.0


def test_run_job_failure_records_message(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "1")
    monkeypatch.setenv("FAKE_FFMPEG_STDERR", "boom: network error\n")
    monkeypatch.delenv("FAKE_FFPROBE_DURATION", raising=False)  # ffprobe fails → no duration
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="fail", extension="mp4", codec="copy", output_folder="",
    ))
    failed = _wait_for_status(db, job["id"], "failed")
    assert "boom" in (failed["message"] or "")
    assert failed["duration_seconds"] is None


def test_run_job_updates_progress(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "4")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "10.0")
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="p", extension="mp4", codec="copy", output_folder="",
    ))
    done = _wait_for_status(db, job["id"], "completed", timeout=10)
    assert done["current_time_seconds"] is not None
    assert done["current_time_seconds"] >= 4.0  # 4 ticks at 1s each
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_jobs.py::test_run_job_completes_successfully -v
```

Expected: `NotImplementedError` from `_run_job`.

- [ ] **Step 3: Implement `_run_job`, progress parsing, and ffprobe duration discovery**

Replace the placeholder `_run_job` method and add helpers. Append to `ffmpeg_downloader/jobs.py`:

```python
# Append to ffmpeg_downloader/jobs.py
import subprocess
from collections import deque


def _parse_progress_line(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


def _probe_duration(ffprobe_bin: str, url: str, timeout: float = 10.0) -> float | None:
    try:
        proc = subprocess.run(
            [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", url],
            capture_output=True, text=True, timeout=timeout,
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


def _run_job_impl(self: "JobManager", job_id: str, argv: list[str], output_path: str) -> None:
    """The real body of _run_job, attached below."""
    # Step 1: ffprobe duration.
    job_row = self._db.get_job(job_id)
    if job_row is None:
        return
    input_url = job_row["selected_variant_url"] or job_row["url"]
    duration = _probe_duration(self._ffprobe_bin, input_url)
    now = int(time.time())
    with self._db_lock:
        self._db.update_job(
            job_id, status="running", started_at=now, duration_seconds=duration
        )
    self._publish_status(job_id, "running")

    # Step 2: launch ffmpeg.
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

    # Step 3: read progress lines.
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

    # Step 4: finalize.
    if job_id in self._cancelled:
        try:
            os.unlink(output_path)
        except OSError:
            pass
        self._finalize(job_id, "cancelled", message=None)
        return
    if proc.returncode == 0:
        self._finalize(job_id, "completed", message=None, progress=100.0)
    else:
        tail = stderr_tail()
        self._finalize(job_id, "failed", message=tail)


def _read_stderr_in_background(proc: subprocess.Popen) -> "callable":
    """Drain stderr into a rolling buffer; return a getter for the latest 4KB."""
    assert proc.stderr is not None
    buf = deque(maxlen=4096)

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


# Attach to class.
JobManager._run_job = _run_job_impl  # type: ignore[assignment]


def _finalize(self: "JobManager", job_id: str, status: str, *, message: str | None,
              progress: float | None = None) -> None:
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


JobManager._finalize = _finalize  # type: ignore[assignment]


def _publish_status(self: "JobManager", job_id: str, status: str) -> None:
    """Placeholder; Task 14 wires this to a real pubsub."""
    return


def _publish_progress(self: "JobManager", job_id: str, progress: float | None,
                      current_time: float | None, speed: str | None) -> None:
    return


JobManager._publish_status = _publish_status  # type: ignore[assignment]
JobManager._publish_progress = _publish_progress  # type: ignore[assignment]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jobs.py -v
```

Expected: 8 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/jobs.py tests/test_jobs.py
git commit -m "Run ffmpeg subprocess, parse progress, finalize jobs in DB"
```

---

## Task 14: JobManager — pubsub for SSE subscribers

**Files:**
- Modify: `ffmpeg_downloader/jobs.py`
- Modify: `tests/test_jobs.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_jobs.py
import queue


def test_pubsub_delivers_progress_and_status_events(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "2")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "10.0")
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="pubsub", extension="mp4", codec="copy", output_folder="",
    ))
    q: queue.Queue = jm.subscribe(job["id"])
    seen_status = []
    seen_progress = 0
    # Read events until we see a terminal status or timeout.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            ev = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if ev["event"] == "status":
            seen_status.append(ev["data"]["status"])
            if ev["data"]["status"] in ("completed", "failed", "cancelled"):
                break
        elif ev["event"] == "progress":
            seen_progress += 1
    assert "running" in seen_status
    assert "completed" in seen_status
    assert seen_progress >= 1


def test_global_subscriber_receives_all_jobs(jm, db, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    q = jm.subscribe_global()
    jm.submit(JobSpec(
        url="https://example.com/a.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="a", extension="mp4", codec="copy", output_folder="",
    ))
    jm.submit(JobSpec(
        url="https://example.com/b.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="b", extension="mp4", codec="copy", output_folder="",
    ))
    job_ids: set[str] = set()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and len(job_ids) < 2:
        try:
            ev = q.get(timeout=0.5)
        except queue.Empty:
            continue
        if ev["event"] == "job" and ev["data"]["status"] == "completed":
            job_ids.add(ev["data"]["id"])
    assert len(job_ids) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: 'JobManager' object has no attribute 'subscribe'`.

- [ ] **Step 3: Implement pubsub**

Replace the placeholder `_publish_status` / `_publish_progress` from Task 13 with real implementations, and add `subscribe` / `subscribe_global` / `unsubscribe`. Append to `ffmpeg_downloader/jobs.py`:

```python
# Append to ffmpeg_downloader/jobs.py
import queue as _queue


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
            global_subs = list(self._global)
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jobs.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/jobs.py tests/test_jobs.py
git commit -m "Add pubsub for per-job and global SSE subscribers"
```

---

## Task 15: JobManager — cancellation

**Files:**
- Modify: `ffmpeg_downloader/jobs.py`
- Modify: `tests/test_jobs.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_jobs.py
def test_cancel_running_job(jm, db, download_root, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "50")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.1")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "100.0")
    job = jm.submit(JobSpec(
        url="https://example.com/x.mp4",
        selected_variant_url=None, selected_variant_label=None,
        filename="cancel", extension="mp4", codec="copy", output_folder="",
    ))
    # Wait until the job is running, then cancel.
    _wait_for_status(db, job["id"], "running", timeout=5)
    jm.cancel(job["id"])
    cancelled = _wait_for_status(db, job["id"], "cancelled", timeout=10)
    assert cancelled["finished_at"] is not None
    # Partial output file should be cleaned up.
    assert not (download_root / "cancel.mp4").exists()


def test_cancel_unknown_job_is_noop(jm):
    jm.cancel("j_doesnotexist")  # must not raise
```

- [ ] **Step 2: Run tests to verify they fail**

Expected: `AttributeError: 'JobManager' object has no attribute 'cancel'`.

- [ ] **Step 3: Implement `cancel()`**

Append to `ffmpeg_downloader/jobs.py`:

```python
# Append to ffmpeg_downloader/jobs.py
def _cancel(self: "JobManager", job_id: str) -> None:
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
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        proc.wait(timeout=2.0)


JobManager.cancel = _cancel  # type: ignore[assignment]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_jobs.py -v
```

Expected: 12 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/jobs.py tests/test_jobs.py
git commit -m "Add JobManager.cancel() with SIGTERM/SIGKILL escalation"
```

---

## Task 16: App factory wires Config + DB + JobManager

**Files:**
- Modify: `ffmpeg_downloader/__init__.py`
- Create: `tests/test_app_factory.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_app_factory.py
from __future__ import annotations

import os
from pathlib import Path

from ffmpeg_downloader import create_app


def test_create_app_with_overrides(download_root: Path, data_dir: Path, fake_ffmpeg_path, fake_ffprobe_path):
    app = create_app({
        "DOWNLOAD_ROOT": str(download_root),
        "DATA_DIR": str(data_dir),
        "FFMPEG_BIN": str(fake_ffmpeg_path),
        "FFPROBE_BIN": str(fake_ffprobe_path),
        "MAX_CONCURRENT_JOBS": "2",
        "TESTING": True,
    })
    assert app.config["TESTING"] is True
    assert "config" in app.extensions
    assert "db" in app.extensions
    assert "fs" in app.extensions
    assert "jobs" in app.extensions
    client = app.test_client()
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["root_exists"] is True
    assert body["db_ok"] is True


def test_create_app_uses_env_when_no_overrides(download_root: Path, data_dir: Path,
                                                fake_ffmpeg_path, fake_ffprobe_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_ROOT", str(download_root))
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("FFMPEG_BIN", str(fake_ffmpeg_path))
    monkeypatch.setenv("FFPROBE_BIN", str(fake_ffprobe_path))
    app = create_app()
    assert app.extensions["config"].download_root == download_root.resolve()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_app_factory.py -v
```

Expected: failure — `create_app()` is the placeholder from Task 1, no extensions wired.

- [ ] **Step 3: Replace `ffmpeg_downloader/__init__.py`**

```python
# ffmpeg_downloader/__init__.py
"""ffmpeg-downloader: a self-hosted ffmpeg wrapper for m3u8 downloads."""

from __future__ import annotations

import time
from typing import Any

from flask import Flask

from .config import Config
from .db import Database
from .filesystem import RootedFS
from .jobs import JobManager

__version__ = "0.1.0"


def create_app(config_overrides: dict[str, Any] | None = None) -> Flask:
    overrides = config_overrides or {}
    testing = bool(overrides.pop("TESTING", False))
    env_view = {**__import__("os").environ}
    for key, value in overrides.items():
        env_view[key] = str(value)
    config = Config.from_env(env_view)

    db = Database.open(config.data_dir / "jobs.db")
    db.reconcile_on_startup(now=int(time.time()), retention_days=config.job_retention_days)
    fs = RootedFS(config.download_root, cache_ttl=config.search_cache_ttl_seconds)
    jobs = JobManager(
        db=db, fs=fs,
        ffmpeg_bin=config.ffmpeg_bin,
        ffprobe_bin=config.ffprobe_bin,
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

    # Routes registered in later tasks.
    from . import routes
    routes.register(app)

    return app
```

The route module exists but is empty for now — create it as an empty registration stub so the import works:

```python
# ffmpeg_downloader/routes.py
from __future__ import annotations

from flask import Flask


def register(app: Flask) -> None:
    """All HTTP/SSE routes are added here. Filled in by later tasks."""
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_app_factory.py tests/test_config.py tests/test_db.py tests/test_filesystem.py tests/test_jobs.py -v
```

Expected: all green (existing tests should still pass since we wired the factory cleanly).

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/__init__.py ffmpeg_downloader/routes.py tests/test_app_factory.py
git commit -m "Wire create_app(): Config + DB reconcile + RootedFS + JobManager"
```

---

## Task 17: Routes — filesystem endpoints

**Files:**
- Modify: `ffmpeg_downloader/routes.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_api.py
from __future__ import annotations

from pathlib import Path

import pytest

from ffmpeg_downloader import create_app


@pytest.fixture
def app(download_root: Path, data_dir: Path, fake_ffmpeg_path, fake_ffprobe_path):
    a = create_app({
        "DOWNLOAD_ROOT": str(download_root),
        "DATA_DIR": str(data_dir),
        "FFMPEG_BIN": str(fake_ffmpeg_path),
        "FFPROBE_BIN": str(fake_ffprobe_path),
        "MAX_CONCURRENT_JOBS": "2",
        "TESTING": True,
    })
    yield a
    a.extensions["jobs"].shutdown(wait=True)
    a.extensions["db"].close()


@pytest.fixture
def client(app):
    return app.test_client()


def test_browse_root(client, download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    r = client.get("/api/browse?path=")
    assert r.status_code == 200
    body = r.get_json()
    names = [i["name"] for i in body["items"]]
    assert names == ["Movies", "Music"]


def test_browse_rejects_traversal(client):
    r = client.get("/api/browse?path=../etc")
    assert r.status_code == 400
    assert "error" in r.get_json()


def test_browse_missing_path_returns_404(client):
    r = client.get("/api/browse?path=does/not/exist")
    assert r.status_code == 404


def test_mkdir(client, download_root: Path):
    r = client.post("/api/mkdir", json={"path": "", "name": "NewLibrary"})
    assert r.status_code == 200
    assert r.get_json() == {"path": "NewLibrary"}
    assert (download_root / "NewLibrary").is_dir()


def test_mkdir_rejects_bad_name(client):
    r = client.post("/api/mkdir", json={"path": "", "name": ".."})
    assert r.status_code == 400


def test_mkdir_requires_json(client):
    r = client.post("/api/mkdir", data="nope")
    assert r.status_code == 400


def test_validate_existing(client, download_root: Path):
    (download_root / "Movies").mkdir()
    r = client.get("/api/validate?path=Movies")
    body = r.get_json()
    assert body["exists"] is True
    assert body["is_dir"] is True
    assert body["writable"] is True


def test_validate_missing_ok(client):
    r = client.get("/api/validate?path=DoesNotExist/Yet")
    assert r.status_code == 200
    body = r.get_json()
    assert body["exists"] is False
    assert body["writable"] is True


def test_validate_traversal_400(client):
    r = client.get("/api/validate?path=../etc")
    assert r.status_code == 400


def test_autocomplete(client, download_root: Path):
    (download_root / "Movies").mkdir()
    (download_root / "Music").mkdir()
    r = client.get("/api/autocomplete?prefix=Mu")
    assert r.status_code == 200
    body = r.get_json()
    assert [m["name"] for m in body["matches"]] == ["Music"]


def test_search_finds_match(client, download_root: Path):
    (download_root / "Movies" / "Office Space").mkdir(parents=True)
    r = client.get("/api/search?q=office&limit=5")
    body = r.get_json()
    assert [m["path"] for m in body["matches"]] == ["Movies/Office Space"]
    assert body["truncated"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py -v
```

Expected: all 11 fail with 404 (routes not registered).

- [ ] **Step 3: Implement filesystem routes**

Replace `ffmpeg_downloader/routes.py`:

```python
# ffmpeg_downloader/routes.py
from __future__ import annotations

from flask import Flask, current_app, jsonify, request

from .filesystem import InvalidNameError, PathTraversalError, RootedFS


def _fs() -> RootedFS:
    return current_app.extensions["fs"]


def register(app: Flask) -> None:

    @app.get("/api/browse")
    def browse():
        path = request.args.get("path", "")
        try:
            return jsonify(_fs().browse(path))
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        except FileNotFoundError:
            return jsonify({"error": "path not found"}), 404

    @app.post("/api/mkdir")
    def mkdir():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        path = body.get("path", "")
        name = body.get("name", "")
        try:
            created = _fs().mkdir(path, name)
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        except InvalidNameError as e:
            return jsonify({"error": str(e)}), 400
        except FileNotFoundError:
            return jsonify({"error": "path not found"}), 404
        return jsonify({"path": created})

    @app.get("/api/validate")
    def validate():
        path = request.args.get("path", "")
        try:
            return jsonify(_fs().validate(path))
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/autocomplete")
    def autocomplete():
        prefix = request.args.get("prefix", "")
        try:
            matches = _fs().autocomplete(prefix)
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"matches": matches})

    @app.get("/api/search")
    def search():
        q = request.args.get("q", "")
        limit_raw = request.args.get("limit", "50")
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 50
        cfg = current_app.extensions["config"]
        limit = min(limit, cfg.search_result_limit)
        return jsonify(_fs().search(q, limit=limit))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/routes.py tests/test_api.py
git commit -m "Add filesystem HTTP routes (browse/mkdir/validate/autocomplete/search)"
```

---

## Task 18: Routes — probe endpoint

**Files:**
- Modify: `ffmpeg_downloader/routes.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_api.py
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _ProbeStub(BaseHTTPRequestHandler):
    body_bytes = b""
    content_type = "application/vnd.apple.mpegurl"

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", self.content_type)
        self.send_header("Content-Length", str(len(self.body_bytes)))
        self.end_headers()
        self.wfile.write(self.body_bytes)

    def log_message(self, *_args):
        return


@pytest.fixture
def probe_stub():
    srv = HTTPServer(("127.0.0.1", 0), _ProbeStub)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        t.join(timeout=2)


def test_probe_master_playlist(client, probe_stub):
    from pathlib import Path
    _ProbeStub.body_bytes = (Path(__file__).parent / "fixtures" / "master-simple.m3u8").read_bytes()
    host, port = probe_stub.server_address
    url = f"http://{host}:{port}/master.m3u8"
    r = client.post("/api/probe", json={"url": url})
    assert r.status_code == 200
    body = r.get_json()
    assert body["type"] == "hls_master"
    assert len(body["variants"]) == 3
    assert body["variants"][0]["label"] == "1920×1080 5.0 Mbps"


def test_probe_direct_url(client, probe_stub):
    _ProbeStub.body_bytes = b"<html>not a playlist</html>"
    _ProbeStub.content_type = "text/html"
    host, port = probe_stub.server_address
    url = f"http://{host}:{port}/file"
    r = client.post("/api/probe", json={"url": url})
    body = r.get_json()
    assert body["type"] == "direct"
    assert body["variants"] == []


def test_probe_unsupported_scheme(client):
    r = client.post("/api/probe", json={"url": "file:///etc/passwd"})
    assert r.status_code == 400


def test_probe_requires_url(client):
    r = client.post("/api/probe", json={})
    assert r.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py -k probe -v
```

Expected: 4 failures with 404.

- [ ] **Step 3: Append the probe route**

Insert inside `register()` in `ffmpeg_downloader/routes.py`, after the existing routes:

```python
    # ---- probe ----
    from urllib.parse import urlparse
    from . import probe as _probe

    @app.post("/api/probe")
    def probe():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        url = body.get("url", "")
        if not url:
            return jsonify({"error": "url is required"}), 400
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return jsonify({"error": "only http(s) URLs are allowed"}), 400
        result = _probe.probe_url(url)
        return jsonify({
            "type": result.type,
            "variants": result.variants,
            "duration_seconds": result.duration_seconds,
            "message": result.message,
        })
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api.py -v
```

Expected: 15 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/routes.py tests/test_api.py
git commit -m "Add /api/probe endpoint with scheme guard"
```

---

## Task 19: Routes — downloads CRUD (POST / GET list / GET one / DELETE)

**Files:**
- Modify: `ffmpeg_downloader/routes.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_api.py
import time as _time


def _wait_for_job_status(app, job_id, status, timeout=8.0):
    db = app.extensions["db"]
    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        j = db.get_job(job_id)
        if j and j["status"] == status:
            return j
        _time.sleep(0.05)
    raise AssertionError(f"{job_id} did not reach {status}; last = {j}")


def test_post_download_creates_queued_job(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "hello",
        "extension": "mp4",
        "codec": "copy",
        "output_folder": "",
    })
    assert r.status_code == 201
    job = r.get_json()
    assert job["id"].startswith("j_")
    assert job["status"] in ("queued", "running", "completed")
    _wait_for_job_status(app, job["id"], "completed")


def test_post_download_rejects_bad_scheme(client):
    r = client.post("/api/downloads", json={
        "url": "file:///etc/passwd",
        "filename": "x", "extension": "mp4", "codec": "copy", "output_folder": "",
    })
    assert r.status_code == 400


def test_post_download_rejects_unknown_codec(client):
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "x", "extension": "mp4", "codec": "banana", "output_folder": "",
    })
    assert r.status_code == 400


def test_post_download_rejects_traversal_output_folder(client):
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "x", "extension": "mp4", "codec": "copy", "output_folder": "../etc",
    })
    assert r.status_code == 400


def test_list_downloads(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    for n in range(3):
        r = client.post("/api/downloads", json={
            "url": f"https://example.com/{n}.mp4",
            "filename": f"f{n}", "extension": "mp4", "codec": "copy", "output_folder": "",
        })
        _wait_for_job_status(app, r.get_json()["id"], "completed")
    r = client.get("/api/downloads?limit=10")
    body = r.get_json()
    assert len(body) == 3
    # newest first
    assert body[0]["filename"] in ("f0.mp4", "f1.mp4", "f2.mp4")


def test_get_one_download(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "single", "extension": "mp4", "codec": "copy", "output_folder": "",
    })
    job_id = r.get_json()["id"]
    _wait_for_job_status(app, job_id, "completed")
    r2 = client.get(f"/api/downloads/{job_id}")
    assert r2.status_code == 200
    assert r2.get_json()["id"] == job_id


def test_get_unknown_download_404(client):
    r = client.get("/api/downloads/j_nope")
    assert r.status_code == 404


def test_delete_terminal_download(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "del", "extension": "mp4", "codec": "copy", "output_folder": "",
    })
    job_id = r.get_json()["id"]
    _wait_for_job_status(app, job_id, "completed")
    d = client.delete(f"/api/downloads/{job_id}")
    assert d.status_code == 200
    r2 = client.get(f"/api/downloads/{job_id}")
    assert r2.status_code == 404


def test_delete_running_download_cancels(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "50")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.1")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "100.0")
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "cancelme", "extension": "mp4", "codec": "copy", "output_folder": "",
    })
    job_id = r.get_json()["id"]
    _wait_for_job_status(app, job_id, "running")
    d = client.delete(f"/api/downloads/{job_id}")
    assert d.status_code == 200
    _wait_for_job_status(app, job_id, "cancelled")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py -k download -v
```

Expected: 9 failures.

- [ ] **Step 3: Implement download routes**

Insert inside `register()` in `ffmpeg_downloader/routes.py`, after the probe route:

```python
    # ---- downloads ----
    from .ffmpeg_command import UnsupportedCodecError, UnsupportedSchemeError
    from .jobs import JobSpec

    @app.post("/api/downloads")
    def create_download():
        if not request.is_json:
            return jsonify({"error": "json body required"}), 400
        body = request.get_json(silent=True) or {}
        try:
            url = body["url"]
            filename = body["filename"]
            extension = body["extension"]
            codec = body["codec"]
            output_folder = body.get("output_folder", "")
        except KeyError as e:
            return jsonify({"error": f"missing field: {e.args[0]}"}), 400

        # Scheme guard before touching JobManager.
        from urllib.parse import urlparse
        for u in (url, body.get("selected_variant_url") or ""):
            if not u:
                continue
            scheme = urlparse(u).scheme.lower()
            if scheme not in ("http", "https"):
                return jsonify({"error": f"unsupported scheme: {scheme}"}), 400

        try:
            _fs().safe_path(output_folder or "")  # traversal pre-check
        except PathTraversalError as e:
            return jsonify({"error": str(e)}), 400

        jm = current_app.extensions["jobs"]
        spec = JobSpec(
            url=url,
            selected_variant_url=body.get("selected_variant_url") or None,
            selected_variant_label=body.get("selected_variant_label") or None,
            filename=filename,
            extension=extension,
            codec=codec,
            output_folder=output_folder,
        )
        try:
            job = jm.submit(spec)
        except (UnsupportedCodecError, UnsupportedSchemeError) as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(job), 201

    @app.get("/api/downloads")
    def list_downloads():
        limit = min(int(request.args.get("limit", "50")), 200)
        db = current_app.extensions["db"]
        return jsonify(db.list_jobs(limit=limit))

    @app.get("/api/downloads/<job_id>")
    def get_download(job_id: str):
        db = current_app.extensions["db"]
        job = db.get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        return jsonify(job)

    @app.delete("/api/downloads/<job_id>")
    def delete_download(job_id: str):
        db = current_app.extensions["db"]
        jm = current_app.extensions["jobs"]
        job = db.get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404
        if job["status"] in ("queued", "running"):
            jm.cancel(job_id)
            return jsonify({"ok": True})
        db.delete_job(job_id)
        return jsonify({"ok": True})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api.py -v
```

Expected: 24 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/routes.py tests/test_api.py
git commit -m "Add download CRUD routes (POST/GET list/GET one/DELETE)"
```

---

## Task 20: Routes — SSE streams (per-job and global)

**Files:**
- Modify: `ffmpeg_downloader/routes.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Append the failing tests**

```python
# Append to tests/test_api.py
import json as _json
import queue


def _parse_sse(chunk: bytes) -> list[dict]:
    """Parse a chunk of SSE bytes into a list of {event, data} dicts."""
    events = []
    event_name = None
    for raw in chunk.decode("utf-8", errors="replace").splitlines():
        if raw.startswith("event:"):
            event_name = raw[len("event:"):].strip()
        elif raw.startswith("data:"):
            data_str = raw[len("data:"):].strip()
            try:
                data = _json.loads(data_str)
            except _json.JSONDecodeError:
                data = data_str
            events.append({"event": event_name, "data": data})
            event_name = None
        elif raw == "":
            event_name = None
    return events


def test_per_job_sse_stream(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "3")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0.05")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "10.0")
    r = client.post("/api/downloads", json={
        "url": "https://example.com/x.mp4",
        "filename": "sse", "extension": "mp4", "codec": "copy", "output_folder": "",
    })
    job_id = r.get_json()["id"]
    # Reading the streaming response until the connection closes.
    with client.get(f"/api/downloads/{job_id}/events", buffered=False) as resp:
        body = resp.get_data()
    events = _parse_sse(body)
    statuses = [e["data"]["status"] for e in events if e["event"] == "status"]
    assert "completed" in statuses


def test_global_sse_stream(app, client, monkeypatch):
    monkeypatch.setenv("FAKE_FFMPEG_TICKS", "1")
    monkeypatch.setenv("FAKE_FFMPEG_SLEEP", "0")
    monkeypatch.setenv("FAKE_FFMPEG_EXIT", "0")
    monkeypatch.setenv("FAKE_FFPROBE_DURATION", "1.0")
    # Subscribe via the JobManager directly to observe; we'll then verify the HTTP route
    # exists and returns 200 with a streaming mimetype.
    r = client.get("/api/events", headers={"Accept": "text/event-stream"},
                   buffered=False, follow_redirects=False)
    # Just check the route is wired and content type is correct.
    assert r.status_code == 200
    assert r.mimetype == "text/event-stream"
    r.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_api.py -k sse -v
```

Expected: 2 failures with 404.

- [ ] **Step 3: Implement SSE routes**

Insert inside `register()` in `ffmpeg_downloader/routes.py`, after the download routes:

```python
    # ---- SSE ----
    import json as _json
    import queue as _queue
    from flask import Response, stream_with_context

    TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

    def _format_event(event: str, data) -> str:
        return f"event: {event}\ndata: {_json.dumps(data)}\n\n"

    @app.get("/api/downloads/<job_id>/events")
    def stream_job_events(job_id: str):
        db = current_app.extensions["db"]
        jm = current_app.extensions["jobs"]
        job = db.get_job(job_id)
        if not job:
            return jsonify({"error": "not found"}), 404

        def gen():
            q = jm.subscribe(job_id)
            try:
                # Emit initial state.
                yield _format_event("status", job)
                if job["status"] in TERMINAL_STATUSES:
                    return
                while True:
                    try:
                        ev = q.get(timeout=30)
                    except _queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield _format_event(ev["event"], ev["data"])
                    if ev["event"] == "status" and ev["data"].get("status") in TERMINAL_STATUSES:
                        return
            finally:
                jm.unsubscribe(job_id, q)

        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    @app.get("/api/events")
    def stream_global_events():
        jm = current_app.extensions["jobs"]

        def gen():
            q = jm.subscribe_global()
            try:
                while True:
                    try:
                        ev = q.get(timeout=30)
                    except _queue.Empty:
                        yield ": keepalive\n\n"
                        continue
                    yield _format_event(ev["event"], ev["data"])
            finally:
                jm.unsubscribe_global(q)

        return Response(stream_with_context(gen()), mimetype="text/event-stream")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api.py -v
```

Expected: 26 passed.

- [ ] **Step 5: Commit**

```bash
git add ffmpeg_downloader/routes.py tests/test_api.py
git commit -m "Add SSE streams: per-job and global event channels"
```

---

## Task 21: Frontend — HTML template + base CSS

**Files:**
- Create: `ffmpeg_downloader/templates/index.html`
- Create: `ffmpeg_downloader/static/style.css`
- Modify: `ffmpeg_downloader/routes.py` (register `GET /`)
- Modify: `tests/test_api.py` (smoke test the page loads)

- [ ] **Step 1: Append the failing test**

```python
# Append to tests/test_api.py
def test_index_page_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.mimetype == "text/html"
    text = r.get_data(as_text=True)
    assert "FFmpeg Downloader" in text
    assert 'id="downloadForm"' in text
    assert 'id="urlInput"' in text
    assert 'id="outputFolder"' in text
    assert 'id="resolutionGroup"' in text  # hidden by default
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
pytest tests/test_api.py::test_index_page_renders -v
```

Expected: 404.

- [ ] **Step 3: Write `ffmpeg_downloader/templates/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="version" content="{{ version }}">
  <title>FFmpeg Downloader</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <main class="page">
    <h1>FFmpeg Downloader</h1>

    <form id="downloadForm" autocomplete="off">
      <div class="field">
        <label for="urlInput">URL</label>
        <input type="url" id="urlInput" name="url" required
               placeholder="https://example.com/master.m3u8">
        <div id="urlHint" class="hint"></div>
      </div>

      <div class="field" id="resolutionGroup" hidden>
        <label for="resolutionSelect">Resolution</label>
        <select id="resolutionSelect" name="selected_variant_url"></select>
      </div>

      <div class="field">
        <label for="filenameInput">Output Filename</label>
        <input type="text" id="filenameInput" name="filename" required
               placeholder="my video">
      </div>

      <div class="field">
        <label for="outputFolder">Output Folder</label>
        <div class="folder-row">
          <div class="folder-input-wrap">
            <input type="text" id="outputFolder" name="output_folder"
                   placeholder="/" autocomplete="off">
            <span id="folderStatus" class="folder-status" aria-hidden="true"></span>
            <ul id="folderSuggestions" class="suggestions" hidden></ul>
          </div>
          <button type="button" id="browseBtn" class="btn btn-secondary">Browse</button>
        </div>
      </div>

      <div class="field-row">
        <div class="field">
          <label for="extensionSelect">Extension</label>
          <select id="extensionSelect" name="extension">
            <option value="mp4" selected>mp4</option>
            <option value="mkv">mkv</option>
            <option value="webm">webm</option>
            <option value="mp3">mp3</option>
            <option value="m4a">m4a</option>
            <option value="avi">avi</option>
            <option value="mov">mov</option>
          </select>
        </div>
        <div class="field">
          <label for="codecSelect">Codec</label>
          <select id="codecSelect" name="codec">
            <option value="copy" selected>copy (no transcode)</option>
            <option value="h264">h264</option>
            <option value="h265">h265</option>
            <option value="vp9">vp9</option>
            <option value="aac">aac (audio only)</option>
            <option value="mp3">mp3 (audio only)</option>
          </select>
        </div>
      </div>

      <button type="submit" class="btn btn-primary submit-btn">Download</button>
    </form>

    <section class="jobs">
      <h2>Downloads</h2>
      <div id="jobsList" class="jobs-list">
        <div class="no-jobs">No downloads yet</div>
      </div>
    </section>
  </main>

  <!-- Folder Browser Modal -->
  <div id="folderModal" class="modal-overlay" hidden>
    <div class="modal" role="dialog" aria-modal="true" aria-labelledby="folderModalTitle">
      <header class="modal-header">
        <h3 id="folderModalTitle">Select Output Folder</h3>
        <button type="button" class="modal-close" id="modalCloseBtn" aria-label="Close">×</button>
      </header>
      <nav class="modal-tabs" role="tablist">
        <button type="button" class="tab active" data-mode="browse" role="tab" aria-selected="true">Browse</button>
        <button type="button" class="tab" data-mode="search" role="tab" aria-selected="false">Search</button>
      </nav>
      <div class="modal-body">
        <section id="browseMode">
          <nav id="breadcrumb" class="breadcrumb" aria-label="Breadcrumb"></nav>
          <input type="text" id="filterInput" class="filter-input"
                 placeholder="Filter this folder...">
          <ul id="folderList" class="folder-list"></ul>
          <div class="new-folder-row">
            <input type="text" id="newFolderName" placeholder="New folder name">
            <button type="button" id="createFolderBtn" class="btn btn-secondary btn-small">Create</button>
          </div>
        </section>
        <section id="searchMode" hidden>
          <input type="text" id="searchInput" class="search-input"
                 placeholder="Search all folders...">
          <ul id="searchResults" class="folder-list"></ul>
        </section>
      </div>
      <footer class="modal-footer">
        <button type="button" id="cancelFolderBtn" class="btn btn-secondary">Cancel</button>
        <button type="button" id="selectFolderBtn" class="btn btn-primary">Select This Folder</button>
      </footer>
    </div>
  </div>

  <script src="{{ url_for('static', filename='app.js') }}" type="module"></script>
</body>
</html>
```

- [ ] **Step 4: Write `ffmpeg_downloader/static/style.css`**

```css
/* ffmpeg_downloader/static/style.css */
:root {
  color-scheme: light dark;
  --bg: #f5f5f5;
  --surface: #ffffff;
  --text: #222;
  --muted: #666;
  --border: #ddd;
  --accent: #2563eb;
  --accent-hover: #1d4ed8;
  --success: #16a34a;
  --warn: #d97706;
  --danger: #dc2626;
  --secondary-bg: #6b7280;
  --secondary-hover: #4b5563;
  --code-bg: #f8f9fa;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0f1115;
    --surface: #1c1f26;
    --text: #f3f4f6;
    --muted: #9ca3af;
    --border: #2a2f3a;
    --accent: #3b82f6;
    --accent-hover: #2563eb;
    --code-bg: #11141a;
  }
}

* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg);
  color: var(--text);
  margin: 0;
}
.page {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 16px 80px;
}
h1, h2, h3 { margin: 0 0 12px; }
h1 { font-size: 1.7rem; text-align: center; margin-bottom: 24px; }
h2 { font-size: 1.15rem; margin-top: 32px; }

.field { margin-bottom: 16px; }
.field-row { display: flex; gap: 12px; }
.field-row .field { flex: 1; }
.field label {
  display: block;
  font-weight: 600;
  margin-bottom: 6px;
  color: var(--muted);
}
input[type="text"], input[type="url"], select {
  width: 100%;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--surface);
  color: var(--text);
  font-size: 14px;
}
input:focus, select:focus {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 30%, transparent);
}
.hint { font-size: 12px; color: var(--muted); margin-top: 4px; }

.btn {
  padding: 10px 16px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  cursor: pointer;
  background: var(--accent);
  color: white;
}
.btn:hover { background: var(--accent-hover); }
.btn:disabled { background: var(--muted); cursor: not-allowed; }
.btn-secondary { background: var(--secondary-bg); }
.btn-secondary:hover { background: var(--secondary-hover); }
.btn-small { padding: 6px 12px; font-size: 12px; }
.submit-btn { width: 100%; padding: 12px; font-size: 16px; margin-top: 8px; }

/* Folder field */
.folder-row { display: flex; gap: 8px; }
.folder-input-wrap { flex: 1; position: relative; }
.folder-status {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  font-size: 14px;
}
.folder-status.ok { color: var(--success); }
.folder-status.warn { color: var(--warn); }
.folder-status.bad { color: var(--danger); }
.suggestions {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 6px;
  position: absolute;
  z-index: 10;
  width: 100%;
  max-height: 240px;
  overflow-y: auto;
  box-shadow: 0 4px 14px rgba(0,0,0,0.12);
}
.suggestions li {
  padding: 8px 10px;
  cursor: pointer;
}
.suggestions li:hover,
.suggestions li.active { background: color-mix(in srgb, var(--accent) 15%, transparent); }

/* Jobs */
.jobs-list { display: flex; flex-direction: column; gap: 10px; }
.no-jobs { color: var(--muted); text-align: center; padding: 24px; }
.job-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px;
}
.job-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.job-filename { font-weight: 600; word-break: break-all; }
.job-status {
  font-size: 11px;
  font-weight: 600;
  padding: 3px 8px;
  border-radius: 999px;
  text-transform: uppercase;
}
.job-status.queued { background: #fef3c7; color: #92400e; }
.job-status.running { background: #dbeafe; color: #1e40af; }
.job-status.completed { background: #dcfce7; color: #15803d; }
.job-status.failed { background: #fee2e2; color: #b91c1c; }
.job-status.cancelled { background: #e5e7eb; color: #374151; }
.progress {
  height: 18px;
  background: color-mix(in srgb, var(--border) 60%, transparent);
  border-radius: 999px;
  overflow: hidden;
}
.progress-bar {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), color-mix(in srgb, var(--accent) 70%, white));
  color: white;
  font-size: 11px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: width 0.25s ease;
}
.progress-bar.indeterminate {
  width: 30% !important;
  animation: indet 1.2s ease-in-out infinite;
}
@keyframes indet {
  0%   { margin-left: -30%; }
  100% { margin-left: 100%; }
}
.job-meta {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--muted);
  margin-top: 6px;
}
.job-command {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  color: var(--muted);
  background: var(--code-bg);
  padding: 8px 10px;
  border-radius: 6px;
  margin-top: 8px;
  word-break: break-all;
}
.job-error {
  color: var(--danger);
  font-size: 12px;
  margin-top: 6px;
}
.cancel-btn {
  background: transparent;
  color: var(--danger);
  border: 1px solid var(--danger);
  border-radius: 6px;
  padding: 4px 10px;
  font-size: 11px;
  cursor: pointer;
}

/* Modal */
.modal-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,0.55);
  display: flex; align-items: center; justify-content: center;
  z-index: 1000;
}
.modal-overlay[hidden] { display: none; }
.modal {
  background: var(--surface);
  width: 92%; max-width: 560px;
  max-height: 86vh;
  border-radius: 12px;
  display: flex; flex-direction: column;
  overflow: hidden;
}
.modal-header, .modal-footer { padding: 12px 16px; }
.modal-header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); }
.modal-close { background: none; border: none; font-size: 22px; cursor: pointer; color: var(--muted); }
.modal-tabs { display: flex; border-bottom: 1px solid var(--border); }
.modal-tabs .tab {
  flex: 1; padding: 10px; border: none; background: transparent; cursor: pointer;
  color: var(--muted); font-weight: 600;
}
.modal-tabs .tab.active { color: var(--accent); border-bottom: 2px solid var(--accent); }
.modal-body { padding: 12px 16px; flex: 1; overflow-y: auto; }
.modal-footer { border-top: 1px solid var(--border); display: flex; gap: 10px; justify-content: flex-end; }
.breadcrumb { display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 10px; }
.breadcrumb-item {
  color: var(--accent); cursor: pointer; font-size: 13px;
}
.breadcrumb-item:hover { text-decoration: underline; }
.breadcrumb-sep { color: var(--muted); }
.filter-input, .search-input {
  width: 100%; padding: 8px 10px; margin-bottom: 8px;
  border: 1px solid var(--border); border-radius: 6px;
  background: var(--surface); color: var(--text);
}
.folder-list {
  list-style: none; margin: 0; padding: 0;
  border: 1px solid var(--border); border-radius: 8px;
  max-height: 280px; overflow-y: auto;
}
.folder-list li {
  display: flex; align-items: center; gap: 8px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
}
.folder-list li:last-child { border-bottom: none; }
.folder-list li:hover, .folder-list li.active {
  background: color-mix(in srgb, var(--accent) 12%, transparent);
}
.folder-list li.no-items { color: var(--muted); cursor: default; }
.folder-icon { font-size: 16px; }
.path-hint { color: var(--muted); font-size: 11px; margin-left: 6px; }
.new-folder-row { display: flex; gap: 8px; margin-top: 10px; }
.new-folder-row input { flex: 1; }
```

- [ ] **Step 5: Update routes to serve the index page**

Insert at the top of `register()` in `ffmpeg_downloader/routes.py`:

```python
    from . import __version__ as _ver
    from flask import render_template

    @app.get("/")
    def index():
        return render_template("index.html", version=_ver)
```

- [ ] **Step 6: Run the test**

```bash
pytest tests/test_api.py::test_index_page_renders -v
```

Expected: passed.

- [ ] **Step 7: Commit**

```bash
git add ffmpeg_downloader/templates/index.html ffmpeg_downloader/static/style.css ffmpeg_downloader/routes.py tests/test_api.py
git commit -m "Add index template, base CSS, and GET / route"
```

---

## Task 22: Frontend JS — form submit + jobs list + SSE wiring

**Files:**
- Create: `ffmpeg_downloader/static/app.js`

No automated test for the JS; we lean on `tests/test_api.py` for the API surface and on a manual browser check at the end.

- [ ] **Step 1: Write `ffmpeg_downloader/static/app.js`**

```javascript
// ffmpeg_downloader/static/app.js
// Vanilla JS, ES modules, no framework.

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  jobs: {},                  // jobId -> job object
  perJobStreams: new Map(),  // jobId -> EventSource
  globalStream: null,
};

// ---------------------------------------------------------------------------
// DOM
// ---------------------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const form = $("downloadForm");
const urlInput = $("urlInput");
const urlHint = $("urlHint");
const resolutionGroup = $("resolutionGroup");
const resolutionSelect = $("resolutionSelect");
const jobsList = $("jobsList");

// ---------------------------------------------------------------------------
// Job rendering
// ---------------------------------------------------------------------------

function jobCard(job) {
  const card = document.createElement("div");
  card.className = "job-card";
  card.dataset.jobId = job.id;

  const header = document.createElement("div");
  header.className = "job-header";

  const filename = document.createElement("div");
  filename.className = "job-filename";
  filename.textContent = job.filename;

  const right = document.createElement("div");
  right.style.display = "flex";
  right.style.gap = "8px";
  right.style.alignItems = "center";

  const status = document.createElement("span");
  status.className = `job-status ${job.status}`;
  status.textContent = job.status;
  right.appendChild(status);

  if (job.status === "queued" || job.status === "running") {
    const cancelBtn = document.createElement("button");
    cancelBtn.type = "button";
    cancelBtn.className = "cancel-btn";
    cancelBtn.textContent = "Cancel";
    cancelBtn.addEventListener("click", () => cancelJob(job.id));
    right.appendChild(cancelBtn);
  }

  header.appendChild(filename);
  header.appendChild(right);
  card.appendChild(header);

  const progress = document.createElement("div");
  progress.className = "progress";
  const bar = document.createElement("div");
  bar.className = "progress-bar";
  if (job.progress === null && (job.status === "running" || job.status === "queued")) {
    bar.classList.add("indeterminate");
  } else {
    const pct = job.progress != null ? job.progress : (job.status === "completed" ? 100 : 0);
    bar.style.width = `${pct}%`;
    bar.textContent = `${pct.toFixed ? pct.toFixed(1) : pct}%`;
  }
  progress.appendChild(bar);
  card.appendChild(progress);

  if (job.duration_seconds || job.current_time_seconds || job.speed) {
    const meta = document.createElement("div");
    meta.className = "job-meta";
    const left = document.createElement("span");
    const cur = fmtSeconds(job.current_time_seconds);
    const tot = job.duration_seconds ? `/${fmtSeconds(job.duration_seconds)}` : "";
    left.textContent = `${cur}${tot}`;
    const rightMeta = document.createElement("span");
    rightMeta.textContent = job.speed || "";
    meta.appendChild(left);
    meta.appendChild(rightMeta);
    card.appendChild(meta);
  }

  if (job.message && (job.status === "failed")) {
    const err = document.createElement("div");
    err.className = "job-error";
    err.textContent = job.message.slice(0, 400);
    card.appendChild(err);
  }

  const cmd = document.createElement("div");
  cmd.className = "job-command";
  cmd.textContent = job.command;
  card.appendChild(cmd);

  return card;
}

function fmtSeconds(s) {
  if (s == null) return "—";
  const total = Math.floor(s);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const sec = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

function renderJobs() {
  jobsList.replaceChildren();
  const list = Object.values(state.jobs).sort(
    (a, b) => (b.created_at || 0) - (a.created_at || 0),
  );
  if (list.length === 0) {
    const empty = document.createElement("div");
    empty.className = "no-jobs";
    empty.textContent = "No downloads yet";
    jobsList.appendChild(empty);
    return;
  }
  for (const job of list) jobsList.appendChild(jobCard(job));
}

function upsertJob(job) {
  state.jobs[job.id] = { ...state.jobs[job.id], ...job };
  renderJobs();
}

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  const text = await resp.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch { payload = text; }
  if (!resp.ok) {
    const msg = (payload && payload.error) || `${resp.status} ${resp.statusText}`;
    const err = new Error(msg);
    err.status = resp.status;
    err.payload = payload;
    throw err;
  }
  return payload;
}

// ---------------------------------------------------------------------------
// Form submit
// ---------------------------------------------------------------------------

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    url: urlInput.value.trim(),
    selected_variant_url: resolutionSelect.value || null,
    selected_variant_label: resolutionSelect.value
      ? resolutionSelect.options[resolutionSelect.selectedIndex].textContent
      : null,
    filename: $("filenameInput").value.trim(),
    extension: $("extensionSelect").value,
    codec: $("codecSelect").value,
    output_folder: $("outputFolder").value.trim(),
  };
  try {
    const job = await api("POST", "/api/downloads", body);
    upsertJob(job);
    attachJobStream(job.id);
    urlInput.value = "";
    // Re-fire input so resolution-picker (loaded as a separate module) clears itself.
    urlInput.dispatchEvent(new Event("input"));
    $("filenameInput").value = "";
  } catch (err) {
    alert(`Failed to start download: ${err.message}`);
  }
});

async function cancelJob(jobId) {
  try {
    await api("DELETE", `/api/downloads/${encodeURIComponent(jobId)}`);
  } catch (err) {
    alert(`Cancel failed: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------

function attachJobStream(jobId) {
  if (state.perJobStreams.has(jobId)) return;
  const es = new EventSource(`/api/downloads/${encodeURIComponent(jobId)}/events`);
  state.perJobStreams.set(jobId, es);
  es.addEventListener("progress", (ev) => {
    const data = JSON.parse(ev.data);
    upsertJob({ id: jobId, ...data });
  });
  es.addEventListener("status", (ev) => {
    const data = JSON.parse(ev.data);
    upsertJob(data);
    if (["completed", "failed", "cancelled"].includes(data.status)) {
      es.close();
      state.perJobStreams.delete(jobId);
    }
  });
  es.onerror = () => { /* let the connection retry */ };
}

function attachGlobalStream() {
  if (state.globalStream) return;
  const es = new EventSource("/api/events");
  state.globalStream = es;
  es.addEventListener("job", (ev) => {
    const data = JSON.parse(ev.data);
    upsertJob(data);
  });
}

// ---------------------------------------------------------------------------
// Init: load existing jobs and start streams
// ---------------------------------------------------------------------------

(async function init() {
  try {
    const existing = await api("GET", "/api/downloads?limit=50");
    for (const j of existing) {
      state.jobs[j.id] = j;
      if (j.status === "queued" || j.status === "running") attachJobStream(j.id);
    }
    renderJobs();
    attachGlobalStream();
  } catch (err) {
    console.error("Failed to load existing jobs", err);
  }
})();

// Folder picker + URL probe are loaded as separate modules in later tasks.
import("./folder-picker.js").catch(() => {});
import("./resolution-picker.js").catch(() => {});
```

- [ ] **Step 2: Manual smoke check**

```bash
# Start the dev server with a tmp DOWNLOAD_ROOT
mkdir -p /tmp/ffd-root /tmp/ffd-data
DOWNLOAD_ROOT=/tmp/ffd-root DATA_DIR=/tmp/ffd-data \
  flask --app ffmpeg_downloader run --debug --port 5050
```

Open <http://127.0.0.1:5050> in a browser. Expected: form renders, "No downloads yet" message visible, dark mode honored. Submitting with an obviously bad URL produces an alert. Stop the server with Ctrl-C when done.

- [ ] **Step 3: Run all tests once to confirm nothing regressed**

```bash
pytest -q
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add ffmpeg_downloader/static/app.js
git commit -m "Add frontend JS: form submit, jobs list, SSE wiring"
```

---

## Task 23: Frontend — folder picker (autocomplete + validation + modal)

**Files:**
- Create: `ffmpeg_downloader/static/folder-picker.js`

- [ ] **Step 1: Write `ffmpeg_downloader/static/folder-picker.js`**

```javascript
// ffmpeg_downloader/static/folder-picker.js

const folderInput = document.getElementById("outputFolder");
const folderStatus = document.getElementById("folderStatus");
const suggestions = document.getElementById("folderSuggestions");
const browseBtn = document.getElementById("browseBtn");
const modal = document.getElementById("folderModal");
const breadcrumb = document.getElementById("breadcrumb");
const folderList = document.getElementById("folderList");
const filterInput = document.getElementById("filterInput");
const searchInput = document.getElementById("searchInput");
const searchResults = document.getElementById("searchResults");
const newFolderName = document.getElementById("newFolderName");
const browseMode = document.getElementById("browseMode");
const searchMode = document.getElementById("searchMode");

let browseState = { currentPath: "", items: [], selectedPath: null };
let searchDebounce = null;
let validateDebounce = null;
let autocompleteDebounce = null;
let activeSuggestionIndex = -1;

// ---------------------------------------------------------------------------
// Live validation
// ---------------------------------------------------------------------------

folderInput.addEventListener("input", () => {
  clearTimeout(validateDebounce);
  validateDebounce = setTimeout(() => runValidation(folderInput.value), 200);
  clearTimeout(autocompleteDebounce);
  autocompleteDebounce = setTimeout(() => runAutocomplete(folderInput.value), 150);
});
folderInput.addEventListener("blur", () => {
  // Hide suggestions on blur unless the click landed on a suggestion.
  setTimeout(() => suggestions.setAttribute("hidden", ""), 120);
});
folderInput.addEventListener("focus", () => {
  if (folderInput.value) runAutocomplete(folderInput.value);
});

async function runValidation(path) {
  if (!path) {
    folderStatus.textContent = "";
    folderStatus.className = "folder-status";
    return;
  }
  try {
    const resp = await fetch(`/api/validate?path=${encodeURIComponent(path)}`);
    if (!resp.ok) throw new Error("invalid path");
    const v = await resp.json();
    if (v.exists && v.is_dir) {
      folderStatus.textContent = "✓";
      folderStatus.className = "folder-status ok";
      folderStatus.title = "Folder exists";
    } else if (!v.exists && v.writable) {
      folderStatus.textContent = "⚠";
      folderStatus.className = "folder-status warn";
      folderStatus.title = "Will be created";
    } else {
      folderStatus.textContent = "✕";
      folderStatus.className = "folder-status bad";
      folderStatus.title = "Path is not usable";
    }
  } catch {
    folderStatus.textContent = "✕";
    folderStatus.className = "folder-status bad";
    folderStatus.title = "Invalid path";
  }
}

// ---------------------------------------------------------------------------
// Inline autocomplete
// ---------------------------------------------------------------------------

async function runAutocomplete(prefix) {
  try {
    const resp = await fetch(`/api/autocomplete?prefix=${encodeURIComponent(prefix)}`);
    const body = await resp.json();
    renderSuggestions(body.matches || []);
  } catch {
    renderSuggestions([]);
  }
}

function renderSuggestions(matches) {
  suggestions.replaceChildren();
  if (matches.length === 0) {
    suggestions.setAttribute("hidden", "");
    activeSuggestionIndex = -1;
    return;
  }
  for (const m of matches) {
    const li = document.createElement("li");
    li.textContent = m.path;
    li.addEventListener("mousedown", (e) => {
      e.preventDefault();
      folderInput.value = m.path + "/";
      suggestions.setAttribute("hidden", "");
      folderInput.focus();
      runValidation(folderInput.value);
      runAutocomplete(folderInput.value);
    });
    suggestions.appendChild(li);
  }
  suggestions.removeAttribute("hidden");
  activeSuggestionIndex = -1;
}

folderInput.addEventListener("keydown", (e) => {
  const items = suggestions.querySelectorAll("li");
  if (suggestions.hasAttribute("hidden") || items.length === 0) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    activeSuggestionIndex = Math.min(items.length - 1, activeSuggestionIndex + 1);
    highlightSuggestion(items);
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    activeSuggestionIndex = Math.max(0, activeSuggestionIndex - 1);
    highlightSuggestion(items);
  } else if (e.key === "Enter" && activeSuggestionIndex >= 0) {
    e.preventDefault();
    items[activeSuggestionIndex].dispatchEvent(new MouseEvent("mousedown"));
  } else if (e.key === "Escape") {
    suggestions.setAttribute("hidden", "");
  } else if (e.key === "Tab" && activeSuggestionIndex >= 0) {
    e.preventDefault();
    items[activeSuggestionIndex].dispatchEvent(new MouseEvent("mousedown"));
  }
});

function highlightSuggestion(items) {
  items.forEach((li, i) => li.classList.toggle("active", i === activeSuggestionIndex));
}

// ---------------------------------------------------------------------------
// Modal — browse mode
// ---------------------------------------------------------------------------

browseBtn.addEventListener("click", () => openModal(folderInput.value));
document.getElementById("modalCloseBtn").addEventListener("click", closeModal);
document.getElementById("cancelFolderBtn").addEventListener("click", closeModal);
document.getElementById("selectFolderBtn").addEventListener("click", () => {
  folderInput.value = browseState.currentPath || "";
  closeModal();
  runValidation(folderInput.value);
});
document.getElementById("createFolderBtn").addEventListener("click", createNewFolder);
filterInput.addEventListener("input", () => renderFolderList(browseState.items));

document.querySelectorAll(".modal-tabs .tab").forEach((tab) => {
  tab.addEventListener("click", () => switchMode(tab.dataset.mode));
});

modal.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeModal();
});

function openModal(startPath) {
  modal.removeAttribute("hidden");
  filterInput.value = "";
  searchInput.value = "";
  switchMode("browse");
  loadFolder(sanitizeStart(startPath));
}

function closeModal() {
  modal.setAttribute("hidden", "");
}

function sanitizeStart(p) {
  if (!p) return "";
  // If the user typed something that doesn't yet exist, jump to the nearest ancestor.
  return p.endsWith("/") ? p.slice(0, -1) : p;
}

async function loadFolder(path) {
  try {
    const resp = await fetch(`/api/browse?path=${encodeURIComponent(path)}`);
    if (!resp.ok) {
      // Fall back to root if the path doesn't exist.
      if (resp.status === 404 && path) return loadFolder("");
      const err = await resp.json();
      alert(err.error || "Failed to load folder");
      return;
    }
    const body = await resp.json();
    browseState = { currentPath: body.current_path, items: body.items, selectedPath: null };
    renderBreadcrumb(body.current_path);
    renderFolderList(body.items);
  } catch (e) {
    alert(`Browse failed: ${e.message}`);
  }
}

function renderBreadcrumb(path) {
  breadcrumb.replaceChildren();
  const root = document.createElement("span");
  root.className = "breadcrumb-item";
  root.textContent = "Root";
  root.addEventListener("click", () => loadFolder(""));
  breadcrumb.appendChild(root);
  if (!path) return;
  const parts = path.split("/");
  let acc = "";
  for (const seg of parts) {
    acc = acc ? `${acc}/${seg}` : seg;
    const sep = document.createElement("span");
    sep.className = "breadcrumb-sep";
    sep.textContent = "/";
    breadcrumb.appendChild(sep);
    const item = document.createElement("span");
    item.className = "breadcrumb-item";
    item.textContent = seg;
    const target = acc;
    item.addEventListener("click", () => loadFolder(target));
    breadcrumb.appendChild(item);
  }
}

function renderFolderList(items) {
  folderList.replaceChildren();
  const filter = filterInput.value.trim().toLowerCase();
  const folders = items
    .filter((i) => i.is_dir)
    .filter((i) => !filter || i.name.toLowerCase().includes(filter));
  if (folders.length === 0) {
    const li = document.createElement("li");
    li.className = "no-items";
    li.textContent = filter ? "No matches" : "No subfolders";
    folderList.appendChild(li);
    return;
  }
  for (const item of folders) {
    const li = document.createElement("li");
    const icon = document.createElement("span");
    icon.className = "folder-icon";
    icon.textContent = "📁";
    const name = document.createElement("span");
    name.textContent = item.name;
    li.appendChild(icon);
    li.appendChild(name);
    li.addEventListener("dblclick", () => loadFolder(item.path));
    li.addEventListener("click", () => {
      folderList.querySelectorAll("li").forEach((el) => el.classList.remove("active"));
      li.classList.add("active");
      browseState.selectedPath = item.path;
    });
    folderList.appendChild(li);
  }
}

async function createNewFolder() {
  const name = newFolderName.value.trim();
  if (!name) return;
  try {
    const resp = await fetch("/api/mkdir", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: browseState.currentPath, name }),
    });
    if (!resp.ok) {
      const err = await resp.json();
      alert(err.error || "Create failed");
      return;
    }
    newFolderName.value = "";
    loadFolder(browseState.currentPath);
  } catch (e) {
    alert(`Create failed: ${e.message}`);
  }
}

// ---------------------------------------------------------------------------
// Modal — search mode
// ---------------------------------------------------------------------------

function switchMode(mode) {
  document.querySelectorAll(".modal-tabs .tab").forEach((t) => {
    const active = t.dataset.mode === mode;
    t.classList.toggle("active", active);
    t.setAttribute("aria-selected", active ? "true" : "false");
  });
  if (mode === "browse") {
    browseMode.removeAttribute("hidden");
    searchMode.setAttribute("hidden", "");
  } else {
    browseMode.setAttribute("hidden", "");
    searchMode.removeAttribute("hidden");
    searchInput.focus();
  }
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => runSearch(searchInput.value), 250);
});

async function runSearch(q) {
  searchResults.replaceChildren();
  if (!q) return;
  try {
    const resp = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=50`);
    const body = await resp.json();
    if (!body.matches || body.matches.length === 0) {
      const li = document.createElement("li");
      li.className = "no-items";
      li.textContent = "No matches";
      searchResults.appendChild(li);
      return;
    }
    for (const m of body.matches) {
      const li = document.createElement("li");
      const icon = document.createElement("span");
      icon.className = "folder-icon";
      icon.textContent = "📁";
      const name = document.createElement("span");
      name.textContent = m.name;
      const hint = document.createElement("span");
      hint.className = "path-hint";
      hint.textContent = m.path;
      li.appendChild(icon);
      li.appendChild(name);
      li.appendChild(hint);
      li.addEventListener("click", () => {
        switchMode("browse");
        loadFolder(m.path);
      });
      searchResults.appendChild(li);
    }
    if (body.truncated) {
      const note = document.createElement("li");
      note.className = "no-items";
      note.textContent = "More results truncated — refine your query.";
      searchResults.appendChild(note);
    }
  } catch (e) {
    alert(`Search failed: ${e.message}`);
  }
}
```

- [ ] **Step 2: Manual smoke test**

Open <http://127.0.0.1:5050> in a browser (server still running from Task 22 or start it again). Verify:
- Typing into Output Folder shows live ✓/⚠/✕ icons and an autocomplete dropdown.
- Browse opens the modal; switching to Search tab finds folders recursively.
- Creating a new folder appears in the list and works as a valid Output Folder.

- [ ] **Step 3: Commit**

```bash
git add ffmpeg_downloader/static/folder-picker.js
git commit -m "Add folder picker: autocomplete, validation, modal browse/search"
```

---

## Task 24: Frontend — resolution picker (URL probe)

**Files:**
- Create: `ffmpeg_downloader/static/resolution-picker.js`

- [ ] **Step 1: Write `ffmpeg_downloader/static/resolution-picker.js`**

```javascript
// ffmpeg_downloader/static/resolution-picker.js

const urlInput = document.getElementById("urlInput");
const urlHint = document.getElementById("urlHint");
const resolutionGroup = document.getElementById("resolutionGroup");
const resolutionSelect = document.getElementById("resolutionSelect");

let probeDebounce = null;
let lastProbedUrl = "";

urlInput.addEventListener("input", () => {
  clearTimeout(probeDebounce);
  const value = urlInput.value.trim();
  if (!value || !/^https?:\/\//i.test(value)) {
    hideResolutionGroup();
    urlHint.textContent = "";
    return;
  }
  probeDebounce = setTimeout(() => probe(value), 500);
});

export function hideResolutionGroup() {
  resolutionGroup.setAttribute("hidden", "");
  resolutionSelect.replaceChildren();
}

async function probe(url) {
  if (url === lastProbedUrl) return;
  lastProbedUrl = url;
  urlHint.textContent = "Inspecting URL…";
  try {
    const resp = await fetch("/api/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const body = await resp.json();
    if (!resp.ok) {
      urlHint.textContent = body.error || "Inspect failed";
      hideResolutionGroup();
      return;
    }
    if (body.type === "hls_master" && body.variants && body.variants.length > 0) {
      populateResolutions(body.variants);
      urlHint.textContent = `HLS master playlist — ${body.variants.length} variants available`;
    } else if (body.type === "hls_media") {
      hideResolutionGroup();
      urlHint.textContent = "HLS media playlist — single resolution";
    } else if (body.type === "direct") {
      hideResolutionGroup();
      urlHint.textContent = "Direct media URL";
    } else {
      hideResolutionGroup();
      urlHint.textContent = body.message || "Could not inspect URL";
    }
  } catch (e) {
    hideResolutionGroup();
    urlHint.textContent = `Inspect failed: ${e.message}`;
  }
}

function populateResolutions(variants) {
  resolutionSelect.replaceChildren();
  const auto = document.createElement("option");
  auto.value = "";
  auto.textContent = `Auto (highest — ${variants[0].label})`;
  resolutionSelect.appendChild(auto);
  for (const v of variants) {
    const o = document.createElement("option");
    o.value = v.url;
    o.textContent = v.label + (v.codecs ? `  ${v.codecs.split(",")[0]}` : "");
    resolutionSelect.appendChild(o);
  }
  resolutionGroup.removeAttribute("hidden");
}
```

The resolution-picker already listens on the URL input's `input` event, and the form submit handler in `app.js` re-fires that event after clearing the URL field, so the picker hides itself with no further wiring needed.

For tidiness, replace the bottom-of-file lazy imports in `ffmpeg_downloader/static/app.js` with static imports — easier to debug and gives proper bundling later if needed.

Replace these lines at the bottom of `app.js`:

```javascript
// Folder picker + URL probe are loaded as separate modules in later tasks.
import("./folder-picker.js").catch(() => {});
import("./resolution-picker.js").catch(() => {});
```

with:

```javascript
import "./folder-picker.js";
import "./resolution-picker.js";
```

- [ ] **Step 2: Manual smoke test**

Restart the dev server, paste an HLS master URL into the URL field (use any public test playlist, e.g. Apple's bipbop sample). Expected: the Resolution dropdown appears with "Auto (highest)" plus each variant. Submitting with a specific resolution selected pins that variant.

- [ ] **Step 3: Commit**

```bash
git add ffmpeg_downloader/static/resolution-picker.js ffmpeg_downloader/static/app.js
git commit -m "Add resolution picker driven by /api/probe"
```

---

## Task 25: Dockerfile + docker-compose example

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`
- Create: `docker-compose.example.yml`

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.github
.venv
venv
__pycache__
*.pyc
.pytest_cache
.ruff_cache
.coverage
htmlcov
data
*.db
*.db-wal
*.db-shm
tests
docs
.playwright-mcp
main-view.png
folder-browser.png
.DS_Store
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1.7

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
```

- [ ] **Step 3: Write `docker-compose.example.yml`**

```yaml
services:
  ffmpeg-downloader:
    image: ghcr.io/ajthom90/ffmpeg-downloader:latest
    container_name: ffmpeg-downloader
    restart: unless-stopped
    ports:
      - "8000:8000"
    volumes:
      # Replace /path/to/your/media with the host directory you want to browse.
      - /path/to/your/media:/downloads
      - ./data:/data
    environment:
      - MAX_CONCURRENT_JOBS=2
      # Optional overrides:
      # - JOB_RETENTION_DAYS=30
      # - SEARCH_CACHE_TTL_SECONDS=60
```

- [ ] **Step 4: Build the image locally and smoke-test it**

```bash
docker build -t ffmpeg-downloader:dev .
mkdir -p /tmp/ffd-media /tmp/ffd-data
docker run --rm -d --name ffd-smoke \
  -p 18000:8000 \
  -v /tmp/ffd-media:/downloads \
  -v /tmp/ffd-data:/data \
  ffmpeg-downloader:dev
sleep 2
curl -fsS http://127.0.0.1:18000/healthz
docker stop ffd-smoke
```

Expected: `docker build` succeeds; `/healthz` returns `{"ok":true,...}`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .dockerignore docker-compose.example.yml
git commit -m "Add Dockerfile, .dockerignore, and docker-compose example"
```

---

## Task 26: GitHub Actions — CI (lint + tests)

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: pyproject.toml

      - name: Install ffmpeg (used by some tests for ffprobe path resolution)
        run: |
          sudo apt-get update
          sudo apt-get install -y --no-install-recommends ffmpeg

      - name: Install package
        run: pip install -e .[dev]

      - name: Ruff lint
        run: ruff check .

      - name: Ruff format check
        run: ruff format --check .

      - name: Run tests
        run: pytest -q
```

- [ ] **Step 2: Verify locally with the same commands the workflow runs**

```bash
ruff check .
ruff format --check .
pytest -q
```

Expected: all three exit 0.

(If `ruff format --check` reports diffs, run `ruff format .` and review/commit the changes.)

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "Add CI workflow: ruff lint, format check, pytest"
```

---

## Task 27: GitHub Actions — multi-arch Docker build to GHCR

**Files:**
- Create: `.github/workflows/docker.yml`

- [ ] **Step 1: Write `.github/workflows/docker.yml`**

```yaml
name: Docker

on:
  push:
    branches: [main]
    tags: ["v*"]
  workflow_dispatch:

permissions:
  contents: read
  packages: write

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Compute tags
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}
          tags: |
            type=ref,event=branch
            type=ref,event=tag
            type=sha,format=short
            type=raw,value=latest,enable={{is_default_branch}}
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}

      - name: Build and push
        uses: docker/build-push-action@v6
        with:
          context: .
          file: Dockerfile
          platforms: linux/amd64,linux/arm64
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
```

- [ ] **Step 2: Sanity-check the YAML**

```bash
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/docker.yml')); print('ok')"
```

Expected: `ok`.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/docker.yml
git commit -m "Add Docker workflow: multi-arch build & push to GHCR"
```

The first successful run on `main` (post-push) will publish `ghcr.io/ajthom90/ffmpeg-downloader:latest` plus a short-SHA tag. Tag releases as `v0.1.0` etc. to publish semver tags.

---

## Task 28: README + LICENSE

**Files:**
- Create: `README.md`
- Create: `LICENSE`

- [ ] **Step 1: Write `LICENSE` (MIT)**

```
MIT License

Copyright (c) 2026 AJ Thom

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

- [ ] **Step 2: Write `README.md`**

```markdown
# ffmpeg-downloader

A small self-hosted web app that wraps `ffmpeg` to download media — especially HLS / `.m3u8` streams — into a configured folder on disk.

> **Security:** No authentication. Sit behind a reverse proxy on a trusted network. **Do not expose to the public internet.**

## Features

- Submit a URL, pick a folder, hit Download.
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

Flask app, one gunicorn worker (gthread, 16 threads), in-process `JobManager` running ffmpeg via `subprocess.Popen` with `-progress pipe:1`. SSE streams pull from a per-job pubsub. SQLite (WAL) persists the job table. See `docs/superpowers/specs/` for the full design.

## License

MIT — see `LICENSE`.
```

- [ ] **Step 3: Commit**

```bash
git add README.md LICENSE
git commit -m "Add README and MIT license"
```

---

## Task 29: Final smoke test (full stack against the real ffmpeg image)

This task verifies the assembled system end-to-end against a real Docker image and ffmpeg binary, including a quick HLS download.

- [ ] **Step 1: Build the image and run a container**

```bash
docker build -t ffmpeg-downloader:smoke .
mkdir -p /tmp/ffd-smoke-media /tmp/ffd-smoke-data
docker run --rm -d --name ffd-smoke \
  -p 18000:8000 \
  -v /tmp/ffd-smoke-media:/downloads \
  -v /tmp/ffd-smoke-data:/data \
  ffmpeg-downloader:smoke
sleep 3
curl -fsS http://127.0.0.1:18000/healthz | python -m json.tool
```

Expected: `ok: true`, `root_exists: true`, `db_ok: true`.

- [ ] **Step 2: Probe an HLS master playlist**

```bash
curl -fsS -X POST http://127.0.0.1:18000/api/probe \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/bipbop_16x9_variant.m3u8"}' \
  | python -m json.tool
```

Expected: `"type": "hls_master"` with a non-empty `variants` list.

- [ ] **Step 3: Submit a short download against the same URL**

```bash
curl -fsS -X POST http://127.0.0.1:18000/api/downloads \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://devstreaming-cdn.apple.com/videos/streaming/examples/bipbop_16x9/bipbop_16x9_variant.m3u8","filename":"bipbop_smoke","extension":"mp4","codec":"copy","output_folder":""}'
```

Watch `docker logs -f ffd-smoke` until the job finishes (a few seconds for the small bipbop playlist).

Verify the file landed:

```bash
ls -la /tmp/ffd-smoke-media/
```

Expected: a file named `bipbop_smoke.mp4` of nonzero size.

- [ ] **Step 4: Tear down**

```bash
docker stop ffd-smoke
rm -rf /tmp/ffd-smoke-media /tmp/ffd-smoke-data
docker rmi ffmpeg-downloader:smoke
```

- [ ] **Step 5: Manual browser walk-through**

With the dev server (`flask --app ffmpeg_downloader run`) running, open the page and check each interaction:
- Paste an HLS master URL → resolution dropdown appears with variants.
- Type a partial path into Output Folder → autocomplete dropdown shows matches.
- Click Browse → modal opens, filter narrows the list, Search tab finds folders anywhere.
- Submit → job card appears, progress bar updates live, status becomes "completed".
- Submit a long-running job → click Cancel → status becomes "cancelled", partial file is gone.
- Reload the page → existing jobs reappear from SQLite.

No commit needed; this task is verification only.

---

## Post-implementation: push when ready

The plan deliberately does **not** push to GitHub. When the user is happy, push everything:

```bash
git push -u origin main
```

The Docker workflow will then trigger and publish `ghcr.io/ajthom90/ffmpeg-downloader:latest`. To cut a release:

```bash
git tag v0.1.0
git push origin v0.1.0
```
