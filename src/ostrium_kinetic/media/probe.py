"""ffprobe of the first video stream, with exact frame rate and duration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ostrium_kinetic.media.ffmpeg import MediaError, Tools, run_ffprobe


@dataclass
class MediaInfo:
    codec: str
    width: int
    height: int
    fps: Fraction
    nb_frames: int
    duration: Fraction
    pix_fmt: str | None
    color_space: str | None
    color_primaries: str | None
    color_transfer: str | None
    color_range: str | None


def probe_args(path: Path, count_frames: bool = True) -> list[str]:
    args = ["-v", "error", "-select_streams", "v:0"]
    if count_frames:
        args.append("-count_frames")
    return [*args, "-show_streams", "-show_format", "-of", "json", str(path)]


def _fraction(value: Any) -> Fraction | None:
    if value in (None, "", "N/A", "0/0"):
        return None
    try:
        return Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        return None


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_probe(data: dict[str, Any], count_frames: bool = True) -> MediaInfo:
    """MediaInfo from `ffprobe -show_streams -show_format -of json` output."""
    streams = [s for s in data.get("streams", []) if s.get("codec_type", "video") == "video"]
    if not streams:
        raise MediaError("no video stream")
    s = streams[0]
    fps = _fraction(s.get("r_frame_rate")) or _fraction(s.get("avg_frame_rate"))
    if fps is None:
        raise MediaError("video stream has no frame rate")
    frames = _int(s.get("nb_read_frames")) if count_frames else None
    if frames is None:
        frames = _int(s.get("nb_frames"))
    duration = _fraction(s.get("duration")) or _fraction(data.get("format", {}).get("duration"))
    if duration is None and frames is not None:
        duration = frames / fps
    if frames is None and duration is not None:
        frames = round(duration * fps)
    if frames is None or duration is None:
        raise MediaError("cannot determine frame count or duration")
    return MediaInfo(
        codec=str(s.get("codec_name", "")),
        width=int(s.get("width", 0)),
        height=int(s.get("height", 0)),
        fps=fps,
        nb_frames=frames,
        duration=duration,
        pix_fmt=s.get("pix_fmt"),
        color_space=s.get("color_space"),
        color_primaries=s.get("color_primaries"),
        color_transfer=s.get("color_transfer"),
        color_range=s.get("color_range"),
    )


def probe(tools: Tools, path: Path, count_frames: bool = True) -> MediaInfo:
    """Probe the first video stream. Counting frames decodes the whole file."""
    if not path.is_file():
        raise MediaError(f"{path}: file not found")
    proc = run_ffprobe(tools, probe_args(path, count_frames), timeout=600.0)
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise MediaError(f"{path}: unreadable ffprobe output") from exc
    try:
        return parse_probe(data, count_frames)
    except MediaError as exc:
        raise MediaError(f"{path}: {exc}") from exc
