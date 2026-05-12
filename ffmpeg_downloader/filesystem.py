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
