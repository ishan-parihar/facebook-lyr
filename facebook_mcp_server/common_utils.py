"""Small shared helpers used across the server modules."""

from __future__ import annotations

import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def slugify_fragment(value: str) -> str:
    """Return a lowercase URL/file-safe fragment."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def utcnow_iso() -> str:
    """Return the current UTC timestamp in a compact ISO-8601 form."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def secure_mkdir(path: Path, mode: int = 0o700) -> None:
    """Create a directory tree with restrictive permissions.

    Unlike ``Path.mkdir(parents=True, mode=...)``, this applies *mode* to
    every newly created directory in the chain, not just the leaf.
    """
    if path.exists() and not path.is_dir():
        raise NotADirectoryError(f"Path exists and is not a directory: {path}")

    missing: list[Path] = []
    p = path
    while not p.exists():
        missing.append(p)
        p = p.parent
    for part in reversed(missing):
        part.mkdir(mode=mode, exist_ok=True)


def secure_write_text(path: Path, content: str, mode: int = 0o600) -> None:
    """Atomically write *content* to *path* with owner-only permissions.

    Uses a temp file + ``os.replace`` in the same directory so the write is
    atomic on the same filesystem and avoids TOCTOU permission races.
    """
    secure_mkdir(path.parent)
    fd_int, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd_int, "w") as f:
            f.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def is_container_runtime() -> bool:
    """Best-effort detection of running inside a container (for diagnostics)."""
    markers = ("docker", "containerd", "kubepods", "podman", "libpod")
    for path in (Path("/.dockerenv"), Path("/run/.containerenv")):
        if path.exists():
            return True
    for probe in (Path("/proc/1/cgroup"), Path("/proc/self/cgroup")):
        if not probe.exists():
            continue
        try:
            text = probe.read_text(encoding="utf-8", errors="ignore").lower()
        except OSError:
            continue
        if any(marker in text for marker in markers):
            return True
    return False
