"""Contact sheets (evenly spaced thumbnails in one PNG) and single-frame extraction."""

from __future__ import annotations

import os
from pathlib import Path

from ostrium_kinetic.job import frame_name
from ostrium_kinetic.media.ffmpeg import MediaError, Tools, atomic_output, run_ffmpeg


def spaced_frames(frame_count: int, n: int) -> list[int]:
    """`n` frame indices spread evenly over the shot, first and last included, no repeats."""
    if frame_count <= 0 or n <= 0:
        return []
    if n == 1:
        return [0]
    if frame_count <= n:
        return list(range(frame_count))
    last = frame_count - 1
    # Round to nearest with integer arithmetic.
    return sorted({(2 * i * last + (n - 1)) // (2 * (n - 1)) for i in range(n)})


def select_expr(frames: list[int]) -> str:
    """ffmpeg `select` expression matching these frame indices (commas escaped)."""
    return "+".join(f"eq(n\\,{f})" for f in frames)


def contact_sheet_args(
    video: Path, out: Path, frames: list[int], columns: int, rows: int, thumb_width: int
) -> list[str]:
    vf = f"select='{select_expr(frames)}',scale={thumb_width}:-2,tile={columns}x{rows}"
    args = ["-i", str(video), "-an", "-vf", vf, "-fps_mode", "passthrough"]
    return [*args, "-frames:v", "1", "-update", "1", str(out)]


def make_contact_sheet(
    tools: Tools,
    video: Path,
    out: Path,
    frame_count: int,
    columns: int = 4,
    rows: int = 4,
    thumb_width: int = 320,
) -> Path:
    """Tile up to columns*rows evenly spaced frames into one PNG (rows shrink to fit short
    shots, so the sheet has no empty cells beyond the last row)."""
    frames = spaced_frames(frame_count, columns * rows)
    if not frames:
        raise MediaError("contact sheet of an empty video")
    rows = min(rows, -(-len(frames) // columns))
    with atomic_output(out) as tmp:
        args = contact_sheet_args(video, tmp, frames, columns, rows, thumb_width)
        run_ffmpeg(tools, args, timeout=600.0)
    return out


def extract_frames(tools: Tools, video: Path, frames: list[int], out_dir: Path) -> list[Path]:
    """Decode the given frame indices to out_dir/frame%08d.png; returns them in index order."""
    wanted = sorted(set(frames))
    if not wanted:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = out_dir / f".extract-{os.getpid()}-%06d.png"
    args = ["-i", str(video), "-an", "-vf", f"select='{select_expr(wanted)}'"]
    args += ["-fps_mode", "passthrough", "-start_number", "0", str(pattern)]
    try:
        run_ffmpeg(tools, args, timeout=600.0)
        temps = [Path(str(pattern) % i) for i in range(len(wanted))]
        if not all(t.is_file() for t in temps) or Path(str(pattern) % len(wanted)).exists():
            got = len(list(out_dir.glob(f".extract-{os.getpid()}-*.png")))
            raise MediaError(f"{video.name}: extracted {got} frames, expected {len(wanted)}")
        result = []
        for index, tmp in zip(wanted, temps, strict=True):
            dst = out_dir / frame_name(index)
            os.replace(tmp, dst)
            result.append(dst)
        return result
    finally:
        for leftover in out_dir.glob(f".extract-{os.getpid()}-*.png"):
            leftover.unlink(missing_ok=True)
