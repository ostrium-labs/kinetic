"""Joining shot mezzanines into a master, and the BT.709 H.264 delivery encode."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from ostrium_kinetic.media.ffmpeg import (
    BITEXACT,
    MediaError,
    Tools,
    atomic_output,
    run_ffmpeg,
)

Quality = Literal["draft", "high"]
Encoder = Literal["libx264", "h264_qsv"]

# M0: without setparams, ffmpeg leaves primaries and transfer `unknown`.
_SETPARAMS = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
COLOR_FLAGS: tuple[str, ...] = (
    "-colorspace",
    "bt709",
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-color_range",
    "tv",
)


def concat_line(path: Path) -> str:
    """One concat-demuxer line: absolute path with forward slashes, single-quoted; a quote in
    the path is written as '\\'' (close, escaped quote, reopen), per ffmpeg's quoting rules."""
    text = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{text}'\n"


def concat_list(mezzanines: list[Path]) -> str:
    return "ffconcat version 1.0\n" + "".join(concat_line(p) for p in mezzanines)


def concat_args(list_file: Path, out: Path) -> list[str]:
    return [
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-map",
        "0:v",
        "-c",
        "copy",
        *BITEXACT,
        str(out),
    ]


def concat_mezzanines(tools: Tools, mezzanines: list[Path], out: Path) -> Path:
    """Stream-copy the mezzanines, in order, into one master (bit-exact per M0)."""
    if not mezzanines:
        raise MediaError("nothing to concatenate")
    missing = [str(p) for p in mezzanines if not p.is_file()]
    if missing:
        raise MediaError("mezzanine not found: " + ", ".join(missing))
    out.parent.mkdir(parents=True, exist_ok=True)
    list_file = out.with_name(f".{out.stem}.concat.txt")
    list_file.write_text(concat_list(mezzanines), encoding="utf-8", newline="\n")
    try:
        with atomic_output(out) as tmp:
            run_ffmpeg(tools, concat_args(list_file, tmp), timeout=600.0)
    finally:
        list_file.unlink(missing_ok=True)
    return out


def delivery_filter(encoder: Encoder = "libx264") -> str:
    """RGB -> Y'CbCr with the BT.709 matrix into limited range, then explicit frame tags.
    The input matrix does not apply: the mezzanine is RGB."""
    fmt = "nv12" if encoder == "h264_qsv" else "yuv420p"
    return f"scale=out_color_matrix=bt709:out_range=tv,format={fmt},{_SETPARAMS}"


def delivery_args(
    master: Path, out: Path, quality: Quality, encoder: Encoder = "libx264"
) -> list[str]:
    args = ["-i", str(master), "-map", "0:v:0", "-vf", delivery_filter(encoder)]
    if encoder == "h264_qsv":
        args += ["-c:v", "h264_qsv", "-global_quality", "23"]
    elif quality == "high":
        args += ["-c:v", "libx264", "-crf", "18", "-preset", "slow"]
    else:
        args += ["-c:v", "libx264", "-crf", "23", "-preset", "veryfast"]
    return [*args, *COLOR_FLAGS, "-movflags", "+faststart", "-an", str(out)]


def encode_delivery(
    tools: Tools,
    master: Path,
    out: Path,
    quality: Quality,
    encoder: Encoder = "libx264",
) -> Path:
    """H.264 MP4, yuv420p, BT.709 tagged, +faststart, no audio stream (M2). Atomic."""
    if not master.is_file():
        raise MediaError(f"{master}: file not found")
    with atomic_output(out) as tmp:
        run_ffmpeg(tools, delivery_args(master, tmp, quality, encoder), timeout=3600.0)
    return out
