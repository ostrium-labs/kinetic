"""Per-shot mezzanine: the frame PNGs losslessly in FFV1 level 3, RGB (bgr0), Matroska."""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from ostrium_kinetic.job import FRAME_PATTERN
from ostrium_kinetic.media.ffmpeg import (
    BITEXACT,
    MediaError,
    Tools,
    atomic_output,
    rational,
    run_ffmpeg,
)
from ostrium_kinetic.media.probe import probe


def mezzanine_args(frames_dir: Path, fps: Fraction, out: Path) -> list[str]:
    """ffmpeg arguments (after the base flags). Every frame is a keyframe (-g 1) with slice
    CRCs, so damage is detected on decode and the master can be cut at any frame."""
    return [
        "-framerate",
        rational(fps),
        "-start_number",
        "0",
        "-i",
        str(frames_dir / FRAME_PATTERN),
        "-map",
        "0:v:0",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-g",
        "1",
        "-slicecrc",
        "1",
        "-pix_fmt",
        "bgr0",
        "-an",
        *BITEXACT,
        str(out),
    ]


def encode_mezzanine(
    tools: Tools, frames_dir: Path, fps: Fraction, out: Path, expected_frames: int
) -> Path:
    """Encode frames_dir/frame%08d.png to `out` atomically and verify the frame count."""
    if not (frames_dir / (FRAME_PATTERN % 0)).is_file():
        raise MediaError(f"{frames_dir}: no {FRAME_PATTERN % 0}")
    timeout = 120.0 + 0.5 * expected_frames
    with atomic_output(out) as tmp:
        run_ffmpeg(tools, mezzanine_args(frames_dir, fps, tmp), timeout)
        info = probe(tools, tmp)
        if info.nb_frames != expected_frames:
            raise MediaError(
                f"{out.name}: mezzanine has {info.nb_frames} frames, expected {expected_frames}"
            )
    return out
