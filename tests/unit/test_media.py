from __future__ import annotations

import struct
import subprocess
import zlib
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest

from ostrium_kinetic.env.hardware import png_size
from ostrium_kinetic.job import frame_name
from ostrium_kinetic.media.assemble import (
    concat_line,
    concat_mezzanines,
    delivery_args,
    encode_delivery,
)
from ostrium_kinetic.media.ffmpeg import MediaError, Tools, ffmpeg_command, rational
from ostrium_kinetic.media.mezzanine import encode_mezzanine, mezzanine_args
from ostrium_kinetic.media.probe import parse_probe, probe
from ostrium_kinetic.qa.contact_sheet import extract_frames, make_contact_sheet, spaced_frames
from ostrium_kinetic.qa.media import (
    check_delivery,
    check_mezzanine,
    detect_black_and_freeze,
    parse_freeze,
)
from ostrium_kinetic.workspace import Workspace

W, H, N = 320, 180, 30
PATCH = (0x3A, 0x7B, 0xD5)  # 58, 123, 213


def write_png(path: Path, rgb: np.ndarray) -> Path:
    """Minimal RGB8 PNG writer (stdlib zlib), so tests control every pixel."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data)
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


# ---------------------------------------------------------------- pure (no ffmpeg)


def test_command_lines() -> None:
    tools = Tools(Path("ffmpeg"), Path("ffprobe"))
    assert ffmpeg_command(tools, ["-i", "x"])[:4] == ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    assert rational(Fraction(30000, 1001)) == "30000/1001" and rational(Fraction(30)) == "30/1"
    mez = mezzanine_args(Path("run/frames"), Fraction(30), Path("m.mkv"))
    joined = " ".join(mez)
    assert "-framerate 30/1 -start_number 0" in joined
    assert "-c:v ffv1 -level 3" in joined and "-pix_fmt bgr0" in joined
    assert str(Path("run/frames") / "frame%08d.png") in mez
    high = delivery_args(Path("m.mkv"), Path("f.mp4"), "high")
    vf = high[high.index("-vf") + 1]
    assert vf == (
        "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
        "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
    )
    assert " ".join(high).endswith(
        "-c:v libx264 -crf 18 -preset slow -colorspace bt709 -color_primaries bt709 "
        "-color_trc bt709 -color_range tv -movflags +faststart -an f.mp4"
    )
    draft = " ".join(delivery_args(Path("m.mkv"), Path("f.mp4"), "draft"))
    assert "-crf 23 -preset veryfast" in draft
    qsv = delivery_args(Path("m.mkv"), Path("f.mp4"), "draft", "h264_qsv")
    assert "format=nv12" in qsv[qsv.index("-vf") + 1]
    assert "-c:v h264_qsv -global_quality 23" in " ".join(qsv)


def test_concat_line_quotes_windows_paths(tmp_path: Path) -> None:
    p = tmp_path / "it's a dir" / "S1 mezz.mkv"
    line = concat_line(p)
    assert line.startswith("file '") and line.endswith("'\n")
    assert "\\" not in line.replace("'\\''", "")  # only the escaped quote uses a backslash
    assert "it'\\''s a dir/S1 mezz.mkv" in line


def test_parse_probe_fallbacks() -> None:
    data = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "ffv1",
                "width": 320,
                "height": 180,
                "r_frame_rate": "30000/1001",
                "nb_read_frames": "30",
                "pix_fmt": "bgr0",
            }
        ],
        "format": {"duration": "1.001000"},
    }
    info = parse_probe(data)
    assert info.fps == Fraction(30000, 1001) and info.nb_frames == 30
    assert info.duration == Fraction(1001, 1000) and info.color_primaries is None
    with pytest.raises(MediaError):
        parse_probe({"streams": []})


def test_spaced_frames() -> None:
    assert spaced_frames(105, 16)[0] == 0 and spaced_frames(105, 16)[-1] == 104
    assert len(spaced_frames(105, 16)) == 16
    assert spaced_frames(5, 16) == [0, 1, 2, 3, 4]
    assert spaced_frames(30, 2) == [0, 29]


def test_parse_freeze_open_ended() -> None:
    err = (
        "[freezedetect @ 0] lavfi.freezedetect.freeze_start: 1\n"
        "[freezedetect @ 0] lavfi.freezedetect.freeze_duration: 2.5\n"
        "[freezedetect @ 0] lavfi.freezedetect.freeze_end: 3.5\n"
        "[freezedetect @ 0] lavfi.freezedetect.freeze_start: 5\n"
    )
    assert parse_freeze(err) == [(1.0, 3.5), (5.0, None)]


# ---------------------------------------------------------------- real ffmpeg


@pytest.fixture(scope="module")
def tools(tmp_path_factory: pytest.TempPathFactory) -> Tools:
    try:
        return Tools.resolve(Workspace(tmp_path_factory.mktemp("ws"), None, {}))
    except MediaError as exc:
        pytest.skip(str(exc))


def _raw_rgb(tools: Tools, args: list[str]) -> bytes:
    cmd = [str(tools.ffmpeg), "-hide_banner", "-nostdin", "-v", "error", *args]
    cmd += ["-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    return subprocess.run(cmd, capture_output=True, check=True, timeout=120).stdout


@pytest.fixture(scope="module")
def testsrc(tools: Tools, tmp_path_factory: pytest.TempPathFactory) -> Path:
    frames = tmp_path_factory.mktemp("testsrc") / "frames"
    frames.mkdir()
    args = ["-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30"]
    args += ["-frames:v", str(N), "-start_number", "0", str(frames / "frame%08d.png")]
    subprocess.run(ffmpeg_command(tools, args), check=True, capture_output=True, timeout=120)
    return frames


@pytest.fixture(scope="module")
def mezzanine(tools: Tools, testsrc: Path) -> Path:
    return encode_mezzanine(tools, testsrc, Fraction(30), testsrc.parent / "mezzanine.mkv", N)


@pytest.mark.renderer("ffmpeg")
def test_mezzanine_round_trip_is_bit_exact(tools: Tools, testsrc: Path, mezzanine: Path) -> None:
    info = probe(tools, mezzanine)
    assert (info.codec, info.pix_fmt, info.nb_frames) == ("ffv1", "bgr0", N)
    assert (info.width, info.height, info.fps) == (W, H, Fraction(30))
    assert not list(mezzanine.parent.glob(".*tmp*"))  # atomic: no temp left behind
    src = _raw_rgb(tools, ["-start_number", "0", "-i", str(testsrc / "frame%08d.png")])
    dec = _raw_rgb(tools, ["-i", str(mezzanine)])
    assert len(src) == W * H * 3 * N and src == dec
    checks = check_mezzanine(tools, mezzanine, W, H, N, "S1")
    assert [c.status for c in checks] == ["pass"]
    assert check_mezzanine(tools, mezzanine, W, H, N + 1, "S1")[0].status == "fail"


@pytest.mark.renderer("ffmpeg")
def test_mezzanine_frame_count_mismatch_is_error(
    tools: Tools, testsrc: Path, tmp_path: Path
) -> None:
    out = tmp_path / "m.mkv"
    with pytest.raises(MediaError, match="expected 31"):
        encode_mezzanine(tools, testsrc, Fraction(30), out, N + 1)
    assert not out.exists() and not list(tmp_path.iterdir())
    with pytest.raises(MediaError):
        encode_mezzanine(tools, tmp_path / "empty", Fraction(30), out, 1)


@pytest.mark.renderer("ffmpeg")
def test_concat_two_mezzanines(tools: Tools, mezzanine: Path, tmp_path: Path) -> None:
    second = tmp_path / "it's here" / "S2.mkv"
    second.parent.mkdir()
    second.write_bytes(mezzanine.read_bytes())
    master = concat_mezzanines(tools, [mezzanine, second], tmp_path / "master.mkv")
    info = probe(tools, master)
    assert info.nb_frames == 2 * N and info.duration == Fraction(2)
    one = _raw_rgb(tools, ["-i", str(mezzanine)])
    assert _raw_rgb(tools, ["-i", str(master)]) == one + one
    assert sorted(p.name for p in tmp_path.iterdir()) == ["it's here", "master.mkv"]
    with pytest.raises(MediaError):
        concat_mezzanines(tools, [tmp_path / "missing.mkv"], tmp_path / "x.mkv")


@pytest.fixture(scope="module")
def patch_master(tools: Tools, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """30 frames: mid-grey with a #3A7BD5 box, as an FFV1 RGB mezzanine."""
    root = tmp_path_factory.mktemp("patch")
    img = np.full((H, W, 3), 128, np.uint8)
    img[45:135, 80:240] = PATCH
    for i in range(N):
        write_png(root / "frames" / frame_name(i), img)
    return encode_mezzanine(tools, root / "frames", Fraction(30), root / "master.mkv", N)


@pytest.mark.renderer("ffmpeg")
@pytest.mark.parametrize("quality", ["draft", "high"])
def test_delivery_bt709_tags_and_colour(tools: Tools, patch_master: Path, quality: str) -> None:
    out = patch_master.parent / f"{quality}.mp4"
    encode_delivery(tools, patch_master, out, quality)  # type: ignore[arg-type]
    checks = check_delivery(tools, out, W, H, Fraction(30), N)
    assert {c.id: c.status for c in checks} == {
        "delivery.size": "pass",
        "delivery.fps": "pass",
        "delivery.frames": "pass",
        "delivery.duration": "pass",
        "delivery.pix_fmt": "pass",
        "delivery.color": "pass",
    }
    # Decode as a BT.709-aware player would and read the middle of the patch.
    vf = "scale=in_color_matrix=bt709:in_range=tv,format=rgb24,crop=40:40:140:70"
    raw = _raw_rgb(tools, ["-i", str(out), "-frames:v", "1", "-vf", vf])
    mean = np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(axis=0)
    assert np.all(np.abs(mean - np.array(PATCH)) <= 2), mean
    wrong = check_delivery(tools, out, W, H, Fraction(25), N + 5)
    bad = {c.id for c in wrong if c.status == "fail"}
    assert bad == {"delivery.fps", "delivery.frames", "delivery.duration"}


@pytest.mark.renderer("ffmpeg")
def test_delivery_untagged_input_fails_colour_check(tools: Tools, tmp_path: Path) -> None:
    out = tmp_path / "plain.mp4"
    args = ["-v", "error", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=30"]
    args += ["-frames:v", "10", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(ffmpeg_command(tools, args), check=True, capture_output=True, timeout=120)
    color = next(c for c in check_delivery(tools, out, W, H, Fraction(30), 10) if "color" in c.id)
    assert color.status == "fail" and "color_primaries" in color.detail
    no_req = check_delivery(tools, out, W, H, Fraction(30), 10, require_bt709=False)
    assert all(c.status == "pass" for c in no_req)


@pytest.mark.renderer("ffmpeg")
def test_black_and_freeze_detection(tools: Tools, mezzanine: Path, tmp_path: Path) -> None:
    rng = np.random.default_rng(1)
    still = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
    frames = tmp_path / "frames"
    for i in range(135):  # 1 s noise, 1 s black, 2.5 s frozen
        if i < 30:
            img = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
        elif i < 60:
            img = np.zeros((H, W, 3), np.uint8)
        else:
            img = still
        write_png(frames / frame_name(i), img)
    clip = encode_mezzanine(tools, frames, Fraction(30), tmp_path / "faulty.mkv", 135)
    found = {c.id: c for c in detect_black_and_freeze(tools, clip, "S1")}
    assert found["media.black"].status == "warn"
    assert found["media.black"].data["ranges"] == [[1.0, 2.0]]
    assert found["media.freeze"].status == "warn"
    (start, _end) = found["media.freeze"].data["ranges"][0]
    assert 1.9 <= start <= 2.1
    clean = detect_black_and_freeze(tools, mezzanine, "S1")
    assert [c.status for c in clean] == ["pass", "pass"]


@pytest.mark.renderer("ffmpeg")
def test_contact_sheet_and_extract(tools: Tools, mezzanine: Path, tmp_path: Path) -> None:
    sheet = make_contact_sheet(tools, mezzanine, tmp_path / "contact.png", N, thumb_width=80)
    assert png_size(sheet) == (4 * 80, 4 * 46)  # 80 x (180*80/320 = 45 -> even 46)
    small = make_contact_sheet(tools, mezzanine, tmp_path / "s.png", 6, columns=4, rows=4)
    assert png_size(small) == (4 * 320, 2 * 180)  # rows shrink to fit 6 frames
    got = extract_frames(tools, mezzanine, [29, 0, 12], tmp_path / "stills")
    assert [p.name for p in got] == [frame_name(0), frame_name(12), frame_name(29)]
    assert all(png_size(p) == (W, H) for p in got)
    assert sorted(p.name for p in (tmp_path / "stills").iterdir()) == [p.name for p in got]
    src = _raw_rgb(tools, ["-i", str(mezzanine)])
    frame_bytes = W * H * 3
    assert _raw_rgb(tools, ["-i", str(got[1])]) == src[12 * frame_bytes : 13 * frame_bytes]
