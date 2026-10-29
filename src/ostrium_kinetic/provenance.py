"""provenance.json: what a film output was made from, and what it is.

The `deterministic` part depends only on inputs (film, plan, traces, scenes, renderer and tool
versions), so two builds of the same inputs give the same `deterministic_sha256`. Output
hashes, the machine, git state and the timestamp are kept separate.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ostrium_kinetic import CONTRACT_VERSION, __version__, canonical

SHOT_KEYS: tuple[str, ...] = (
    "shot_id",
    "render_key",
    "trace_sha256",
    "scene_sha256",
    "renderer",
    "renderer_version",
)


def file_sha256(path: Path) -> str:
    """Raw-byte hash (media files: no line-ending normalisation)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _rel(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _git(directory: Path, *args: str) -> str | None:
    git = shutil.which("git")
    if git is None or not directory.is_dir():
        return None
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            [git, "-C", str(directory), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15.0,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout if proc.returncode == 0 else None


def git_state(directory: Path) -> tuple[str | None, bool | None]:
    """(HEAD sha, dirty) of the repository containing `directory`; (None, None) if none."""
    head = _git(directory, "rev-parse", "HEAD")
    if head is None:
        return None, None
    status = _git(directory, "status", "--porcelain")
    return head.strip(), (None if status is None else bool(status.strip()))


GitProbe = Callable[[Path], tuple[str | None, bool | None]]


def build_provenance(
    *,
    film_id: str,
    film_sha256: str,
    profile: str,
    plan_sha256: str,
    shots: list[dict[str, Any]],
    tools: Mapping[str, str | None],
    outputs: list[Path],
    workspace_root: Path,
    kinetic_root: Path,
    git: GitProbe = git_state,
) -> dict[str, Any]:
    """Provenance record. `shots` are dicts carrying SHOT_KEYS (others are ignored)."""
    deterministic = {
        "film": {"id": film_id, "sha256": film_sha256},
        "profile": profile,
        "plan_sha256": plan_sha256,
        "shots": [{k: shot.get(k) for k in SHOT_KEYS} for shot in shots],
        "tools": {name: tools[name] for name in sorted(tools)},
        "kinetic_version": __version__,
        "contract": CONTRACT_VERSION,
    }
    kinetic_sha, kinetic_dirty = git(kinetic_root)
    workspace_sha, workspace_dirty = git(workspace_root)
    return {
        "deterministic": deterministic,
        "deterministic_sha256": canonical.sha256_json(deterministic),
        "outputs": [
            {
                "path": _rel(p, workspace_root),
                "sha256": file_sha256(p),
                "bytes": p.stat().st_size,
            }
            for p in outputs
        ],
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "git": {
                "kinetic": kinetic_sha,
                "kinetic_dirty": kinetic_dirty,
                "workspace": workspace_sha,
                "workspace_dirty": workspace_dirty,
            },
        },
        "created_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
    }


def write_provenance(path: Path, data: Mapping[str, Any]) -> Path:
    """Pretty, key-sorted JSON with LF line endings, written atomically."""
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    canonical.write_text_lf(tmp, text + "\n")
    os.replace(tmp, path)
    return path
