"""Media QA with ffprobe/ffmpeg: mezzanine and delivery structure, colour tags, black and
frozen stretches."""

from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

from ostrium_kinetic.canonical import fraction_str
from ostrium_kinetic.media.ffmpeg import MediaError, Tools, run_ffmpeg
from ostrium_kinetic.media.probe import MediaInfo, probe
from ostrium_kinetic.qa.report import QaCheck, check

BT709_TAGS: dict[str, str] = {
    "color_space": "bt709",
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_range": "tv",
}
BLACKDETECT = "blackdetect=d=0.5:pix_th=0.10"
FREEZEDETECT = "freezedetect=n=0.001:d=2"


def _probe(
    tools: Tools, path: Path, id: str, shot_id: str | None
) -> tuple[MediaInfo | None, QaCheck | None]:
    try:
        return probe(tools, path), None
    except MediaError as exc:
        return None, check(id, shot_id, "fail", str(exc))


def check_mezzanine(
    tools: Tools, path: Path, width: int, height: int, frame_count: int, shot_id: str
) -> list[QaCheck]:
    """FFV1, RGB (bgr0), the shot's size and exact frame count."""
    info, failed = _probe(tools, path, "mezzanine", shot_id)
    if info is None:
        assert failed is not None
        return [failed]
    problems = []
    if info.codec != "ffv1":
        problems.append(f"codec {info.codec}, want ffv1")
    if info.pix_fmt != "bgr0":
        problems.append(f"pix_fmt {info.pix_fmt}, want bgr0")
    if (info.width, info.height) != (width, height):
        problems.append(f"size {info.width}x{info.height}, want {width}x{height}")
    if info.nb_frames != frame_count:
        problems.append(f"{info.nb_frames} frames, want {frame_count}")
    return [
        check(
            "mezzanine",
            shot_id,
            "fail" if problems else "pass",
            "; ".join(problems) or f"ffv1 bgr0 {width}x{height}, {frame_count} frames",
            codec=info.codec,
            pix_fmt=info.pix_fmt,
            frames=info.nb_frames,
        )
    ]


def check_delivery(
    tools: Tools,
    path: Path,
    width: int,
    height: int,
    fps: Fraction,
    frame_count: int,
    require_bt709: bool = True,
) -> list[QaCheck]:
    """Size, exact fps, frame count, duration within one frame, yuv420p and BT.709 tags."""
    info, failed = _probe(tools, path, "delivery.probe", None)
    if info is None:
        assert failed is not None
        return [failed]
    fps = Fraction(fps)
    want_duration = Fraction(frame_count) / fps
    drift = abs(info.duration - want_duration)

    def one(id: str, ok: bool, good: str, bad: str, **data: object) -> QaCheck:
        return check(id, None, "pass" if ok else "fail", good if ok else bad, **data)

    out = [
        one(
            "delivery.size",
            (info.width, info.height) == (width, height),
            f"{width}x{height}",
            f"{info.width}x{info.height}, want {width}x{height}",
        ),
        one(
            "delivery.fps",
            info.fps == fps,
            f"{fraction_str(fps)} fps",
            f"{fraction_str(info.fps)} fps, want {fraction_str(fps)}",
        ),
        one(
            "delivery.frames",
            info.nb_frames == frame_count,
            f"{frame_count} frames",
            f"{info.nb_frames} frames, want {frame_count}",
        ),
        one(
            "delivery.duration",
            drift <= 1 / fps,
            f"{float(info.duration):.3f} s (want {float(want_duration):.3f} s +-1 frame)",
            f"{float(info.duration):.3f} s, want {float(want_duration):.3f} s +-1 frame",
            duration=fraction_str(info.duration),
        ),
        one(
            "delivery.pix_fmt",
            info.pix_fmt == "yuv420p",
            "yuv420p",
            f"pix_fmt {info.pix_fmt}, want yuv420p",
        ),
    ]
    if require_bt709:
        tags = {
            "color_space": info.color_space,
            "color_primaries": info.color_primaries,
            "color_transfer": info.color_transfer,
            "color_range": info.color_range,
        }
        bad = [
            f"{k}={v or 'missing'} (want {BT709_TAGS[k]})"
            for k, v in tags.items()
            if v != BT709_TAGS[k]
        ]
        out.append(
            one("delivery.color", not bad, "bt709/bt709/bt709, range tv", ", ".join(bad), **tags)
        )
    return out


_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")


def detect_args(path: Path) -> list[str]:
    vf = f"{BLACKDETECT},{FREEZEDETECT}"
    return ["-i", str(path), "-map", "0:v:0", "-vf", vf, "-an", "-f", "null", "-"]


def parse_black(stderr: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in _BLACK.findall(stderr)]


def parse_freeze(stderr: str) -> list[tuple[float, float | None]]:
    """(start, end) ranges; end None when the video ends frozen and no end was logged."""
    ranges: list[tuple[float, float | None]] = []
    for line in stderr.splitlines():
        if m := _FREEZE_START.search(line):
            ranges.append((float(m.group(1)), None))
        elif (m := _FREEZE_END.search(line)) and ranges and ranges[-1][1] is None:
            ranges[-1] = (ranges[-1][0], float(m.group(1)))
    return ranges


def _ranges(ranges: list[tuple[float, float | None]]) -> str:
    return ", ".join(f"{a:.2f}-{'end' if b is None else f'{b:.2f}'} s" for a, b in ranges)


def detect_black_and_freeze(tools: Tools, path: Path, shot_id: str | None = None) -> list[QaCheck]:
    """blackdetect (>= 0.5 s) and freezedetect (>= 2 s): warnings with time ranges."""
    try:
        proc = run_ffmpeg(tools, detect_args(path), timeout=1800.0)
    except MediaError as exc:
        return [check("media.black", shot_id, "fail", str(exc))]
    black = parse_black(proc.stderr)
    frozen = parse_freeze(proc.stderr)
    return [
        check(
            "media.black",
            shot_id,
            "warn" if black else "pass",
            f"black: {_ranges(list(black))}" if black else "no black stretch >= 0.5 s",
            ranges=[list(r) for r in black],
        ),
        check(
            "media.freeze",
            shot_id,
            "warn" if frozen else "pass",
            f"frozen: {_ranges(frozen)}" if frozen else "no frozen stretch >= 2 s",
            ranges=[list(r) for r in frozen],
        ),
    ]
