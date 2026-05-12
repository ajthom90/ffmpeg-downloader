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
        if os.path.isabs(rel):
            abs_path = Path(rel)
            if abs_path.exists():
                resolved_abs = abs_path.resolve()
                if resolved_abs != self.root and self.root not in resolved_abs.parents:
                    raise PathTraversalError(f"path escapes root: {rel!r}")
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
            items.append(
                {
                    "name": entry.name,
                    "path": self.rel(Path(entry.path)),
                    "is_dir": entry.is_dir(follow_symlinks=False),
                }
            )
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
