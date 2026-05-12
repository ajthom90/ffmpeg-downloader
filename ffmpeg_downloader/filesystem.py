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
        # If `rel` was an absolute path to a real filesystem entity outside
        # root, reject it. Without this, lstrip("/") would silently turn
        # `/var/folders/.../outside` into a fake nested path under root.
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
        """Return path relative to root, as a forward-slash string."""
        p = p.resolve()
        if p == self.root:
            return ""
        return p.relative_to(self.root).as_posix()


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
        items.append(
            {
                "name": entry.name,
                "path": self.rel(Path(entry.path)),
                "is_dir": entry.is_dir(follow_symlinks=False),
            }
        )
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
