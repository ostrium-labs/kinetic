"""Hardware facts, encoder parsing, PNG headers and the deep-probe helpers."""

from __future__ import annotations

import shutil
import struct
import zlib
from pathlib import Path

import pytest

from ostrium_kinetic.env.hardware import (
    disk_status,
    hardware_tier,
    is_discrete,
    parse_lspci,
    parse_meminfo,
    parse_win32_video,
    png_size,
    total_ram_bytes,
)
from ostrium_kinetic.env.probes import (
    color_tag_mismatches,
    encode_args,
    ffmpeg_error_summary,
    parse_encoders,
    verify_frames,
)


def test_parse_win32_video_single_object_and_list() -> None:
    one = '{"Name": "Intel(R) Iris(R) Xe Graphics", "DriverVersion": "32.0.101.7088"}'
    gpus = parse_win32_video(one)
    assert len(gpus) == 1
    assert gpus[0].vendor == "intel" and not gpus[0].discrete
    assert gpus[0].driver == "32.0.101.7088"
    two = (
        '[{"Name": "Intel(R) UHD Graphics 630", "DriverVersion": "1"},'
        ' {"Name": "NVIDIA GeForce RTX 4070", "DriverVersion": "2"}]'
    )
    gpus = parse_win32_video(two)
    assert [g.vendor for g in gpus] == ["intel", "nvidia"]
    assert [g.discrete for g in gpus] == [False, True]
    assert parse_win32_video("") == []


def test_discrete_classification() -> None:
    assert is_discrete("AMD Radeon RX 7800 XT")
    assert not is_discrete("AMD Radeon(TM) Graphics")
    assert not is_discrete("Intel(R) Iris(R) Xe Graphics")


def test_parse_lspci() -> None:
    text = (
        "00:02.0 VGA compatible controller: Intel Corporation TigerLake-LP GT2 [Iris Xe Graphics]\n"
        "01:00.0 3D controller: NVIDIA Corporation GA107M [GeForce RTX 3050 Mobile]\n"
        "00:1f.3 Audio device: Intel Corporation Tiger Lake-LP Smart Sound\n"
    )
    gpus = parse_lspci(text)
    assert [g.vendor for g in gpus] == ["intel", "nvidia"]
    assert gpus[1].discrete


def test_meminfo_and_tier() -> None:
    assert parse_meminfo("MemTotal:       16303428 kB\nMemFree: 1 kB\n") == 16303428 * 1024
    assert parse_meminfo("nothing") is None
    assert hardware_tier(False, 15.7) == "laptop-igpu"
    assert hardware_tier(False, 32.0) == "workstation"
    assert hardware_tier(True, 15.7) == "workstation"


def test_total_ram_is_plausible() -> None:
    ram = total_ram_bytes()
    assert ram is None or ram > 512 * 1024**2


@pytest.mark.parametrize(
    ("free", "want"),
    [(208.0, "PASS"), (50.0, "PASS"), (49.9, "WARN"), (15.0, "WARN"), (14.9, "FAIL"), (0, "FAIL")],
)
def test_disk_thresholds(free: float, want: str) -> None:
    assert disk_status(free) == want


ENCODERS_SAMPLE = """\
Encoders:
 V..... = Video
 A..... = Audio
 ------
 V....D a64multi             Multicolor charset for Commodore 64 (codec a64_multi)
 V..... libsvtav1            SVT-AV1(Scalable Video Technology for AV1) encoder (codec av1)
 V.S..D ffv1                 FFmpeg video codec #1
 V....D libx264              libx264 H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (codec h264)
 V..... h264_qsv             H.264 / AVC / MPEG-4 AVC / MPEG-4 part 10 (Intel Quick Sync)
 VFS... prores_ks            Apple ProRes (iCodec Pro) (codec prores)
 A....D aac                  AAC (Advanced Audio Coding)
"""


def test_parse_encoders() -> None:
    names = parse_encoders(ENCODERS_SAMPLE)
    assert {"libx264", "libsvtav1", "ffv1", "h264_qsv", "prores_ks", "aac"} <= names
    assert "=" not in names and "Video" not in names


def test_encode_args_qsv_uses_nv12() -> None:
    qsv = encode_args("ffmpeg", "h264_qsv")
    assert qsv[qsv.index("-pix_fmt") + 1] == "nv12"
    assert qsv[-3:] == ["-f", "null", "-"]
    assert "-pix_fmt" not in encode_args("ffmpeg", "libx264")


def test_ffmpeg_error_summary_picks_encoder_line() -> None:
    text = (
        "[h264_nvenc @ 00000218fcee2ac0] Cannot load nvcuda.dll\n"
        "[vost#0:0/h264_nvenc @ 00000218fcee27c0] [enc:h264_nvenc @ 0000] Error while opening\n"
        "[out#0/null @ 00000218fce18680] Nothing was written into output file\n"
    )
    assert ffmpeg_error_summary(text, "h264_nvenc") == "Cannot load nvcuda.dll"
    assert ffmpeg_error_summary("", "x") is None


def test_color_tag_mismatches() -> None:
    good = {
        "color_space": "bt709",
        "color_primaries": "bt709",
        "color_transfer": "bt709",
        "color_range": "tv",
    }
    assert color_tag_mismatches(good) == []
    # The M0 bug: primaries and transfer dropped without setparams.
    bad = {**good, "color_primaries": "unknown"}
    del bad["color_transfer"]
    assert color_tag_mismatches(bad) == [
        "color_primaries=unknown (want bt709)",
        "color_transfer=missing (want bt709)",
    ]


def _png(path: Path, width: int, height: int, payload: int = 0) -> Path:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    body = chunk(b"IHDR", ihdr) + chunk(b"tEXt", b"x" * payload) + chunk(b"IEND", b"")
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + body)
    return path


def test_png_size(tmp_path: Path) -> None:
    assert png_size(_png(tmp_path / "a.png", 320, 180)) == (320, 180)
    (tmp_path / "b.png").write_bytes(b"not a png at all, definitely not")
    assert png_size(tmp_path / "b.png") is None
    assert png_size(tmp_path / "missing.png") is None


def test_verify_frames(tmp_path: Path) -> None:
    frames = [_png(tmp_path / f"f{i}.png", 320, 180, 2000 if i == 3 else 0) for i in range(10)]
    assert verify_frames(frames, 10, (320, 180)) is None
    assert "expected 10" in (verify_frames(frames[:9], 10, (320, 180)) or "")
    small = [_png(tmp_path / f"s{i}.png", 320, 180) for i in range(10)]
    assert "blank" in (verify_frames(small, 10, (320, 180)) or "")
    wrong = [*frames[:9], _png(tmp_path / "w.png", 1152, 648, 2000)]
    assert "w.png" in (verify_frames(wrong, 10, (320, 180)) or "")


@pytest.mark.renderer("ffmpeg")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_real_ffmpeg_lists_libx264() -> None:
    from ostrium_kinetic.env.probes import list_encoders

    ffmpeg = shutil.which("ffmpeg")
    assert ffmpeg is not None
    names, error = list_encoders(ffmpeg)
    assert error is None and "ffv1" in names


@pytest.mark.renderer("ffmpeg")
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not on PATH",
)
def test_real_bt709_chain(tmp_path: Path) -> None:
    from ostrium_kinetic.env.probes import check_bt709

    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg is not None and ffprobe is not None
    if "libx264" not in parse_encoders_for(ffmpeg):
        pytest.skip("ffmpeg build without libx264")
    result = check_bt709(ffmpeg, ffprobe, tmp_path)
    assert result.ok, result.detail


def parse_encoders_for(ffmpeg: str) -> set[str]:
    from ostrium_kinetic.env.probes import list_encoders

    return list_encoders(ffmpeg)[0]


def test_godot_probe_project_uses_a_fixed_user_dir(tmp_path: Path) -> None:
    """Without a custom user dir every probe run would leave a new app_userdata folder."""
    from ostrium_kinetic.env.probes import write_godot_probe

    write_godot_probe(tmp_path / "p")
    text = (tmp_path / "p" / "project.godot").read_text("utf-8")
    application = text.split("[application]", 1)[1].split("\n[", 1)[0]
    assert "config/use_custom_user_dir=true" in application
    assert 'config/custom_user_dir_name="kinetic"' in application
