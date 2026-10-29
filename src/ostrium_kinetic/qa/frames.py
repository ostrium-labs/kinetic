"""Structural checks of a shot's frame directory: count, numbering, size, integrity."""

from __future__ import annotations

import re
from pathlib import Path

from ostrium_kinetic.env.hardware import png_size
from ostrium_kinetic.qa.report import QaCheck, check

_FRAME_RE = re.compile(r"^frame(\d{8})\.png$")
_IEND = b"\x00\x00\x00\x00IEND\xaeB`\x82"
MIN_PNG_BYTES = 67  # signature + IHDR + one IDAT + IEND
SHOW = 5  # how many offending names a detail lists


def _first(items: list[str]) -> str:
    more = f" (+{len(items) - SHOW} more)" if len(items) > SHOW else ""
    return ", ".join(items[:SHOW]) + more


def _complete_png(path: Path, size: int) -> bool:
    """Ends with an IEND chunk: catches frames truncated by a crashed writer."""
    if size < MIN_PNG_BYTES:
        return False
    try:
        with path.open("rb") as fh:
            fh.seek(-len(_IEND), 2)
            return fh.read() == _IEND
    except OSError:
        return False


def check_frames(
    frames_dir: Path, frame_count: int, width: int, height: int, shot_id: str
) -> list[QaCheck]:
    """frames.count, frames.numbering, frames.size and frames.integrity for one shot."""
    if not frames_dir.is_dir():
        return [check("frames.count", shot_id, "fail", f"{frames_dir} does not exist")]
    indices: dict[int, Path] = {}
    stray_png: list[str] = []
    other: list[str] = []
    for p in sorted(frames_dir.iterdir()):
        m = _FRAME_RE.match(p.name)
        if m and p.is_file():
            indices[int(m.group(1))] = p
        elif p.suffix.lower() == ".png":
            stray_png.append(p.name)
        else:
            other.append(p.name)

    out: list[QaCheck] = []
    found = len(indices) + len(stray_png)
    out.append(
        check(
            "frames.count",
            shot_id,
            "pass" if found == frame_count else "fail",
            f"{found} PNGs, expected {frame_count}",
            found=found,
            expected=frame_count,
        )
    )

    missing = [f"frame{i:08d}.png" for i in range(frame_count) if i not in indices]
    extra = [p.name for i, p in sorted(indices.items()) if i >= frame_count] + stray_png
    problems = []
    if missing:
        problems.append(f"{len(missing)} missing: {_first(missing)}")
    if extra:
        problems.append(f"{len(extra)} unexpected: {_first(extra)}")
    out.append(
        check(
            "frames.numbering",
            shot_id,
            "fail" if problems else "pass",
            "; ".join(problems) or f"frame00000000..frame{frame_count - 1:08d} contiguous",
            missing=missing[:50],
            extra=extra[:50],
        )
    )
    if other:
        out.append(
            check(
                "frames.stray_files",
                shot_id,
                "warn",
                f"{len(other)} non-PNG files in the frames directory: {_first(other)}",
                files=other[:50],
            )
        )

    wrong_size: list[str] = []
    broken: list[str] = []
    for _, p in sorted(indices.items()):
        got = png_size(p)
        if got is None:
            broken.append(f"{p.name} (not a PNG)")
            continue
        if got != (width, height):
            wrong_size.append(f"{p.name} {got[0]}x{got[1]}")
        nbytes = p.stat().st_size
        if not _complete_png(p, nbytes):
            broken.append(f"{p.name} ({nbytes} bytes, truncated)")
    out.append(
        check(
            "frames.size",
            shot_id,
            "fail" if wrong_size else "pass",
            f"{len(wrong_size)} frames not {width}x{height}: {_first(wrong_size)}"
            if wrong_size
            else f"all {len(indices)} frames {width}x{height}",
            wrong=wrong_size[:50],
        )
    )
    out.append(
        check(
            "frames.integrity",
            shot_id,
            "fail" if broken else "pass",
            f"{len(broken)} unreadable frames: {_first(broken)}"
            if broken
            else "every frame is a complete PNG",
            broken=broken[:50],
        )
    )
    return out
