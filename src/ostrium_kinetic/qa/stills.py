"""Keep chosen frames (checkpoints, contact-sheet picks) before the PNGs are deleted."""

from __future__ import annotations

import shutil
from collections.abc import Iterable
from pathlib import Path

from ostrium_kinetic.job import frame_name


def keep_stills(frames_dir: Path, stills_dir: Path, frames: Iterable[int]) -> list[Path]:
    """Copy frame%08d.png for each requested index into stills_dir (same names, sorted,
    duplicates once). Raises FileNotFoundError naming the first missing frame."""
    stills_dir.mkdir(parents=True, exist_ok=True)
    kept: list[Path] = []
    for index in sorted(set(frames)):
        src = frames_dir / frame_name(index)
        if not src.is_file():
            raise FileNotFoundError(f"cannot keep still: {src} does not exist")
        dst = stills_dir / src.name
        shutil.copy2(src, dst)
        kept.append(dst)
    return kept
