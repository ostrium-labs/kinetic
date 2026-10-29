"""Real test runs for `kinetic doctor --deep`: encodes, the BT.709 chain, and tiny renders."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ostrium_kinetic.env.hardware import png_size
from ostrium_kinetic.env.tools import Runner, run

WANTED_ENCODERS: tuple[str, ...] = (
    "libx264",
    "libsvtav1",
    "ffv1",
    "prores_ks",
    "h264_qsv",
    "hevc_qsv",
    "av1_qsv",
    "h264_nvenc",
    "h264_amf",
)

# M0: without `setparams` ffmpeg drops the primaries/transfer tags.
BT709_VF = (
    "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
    "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv"
)
BT709_EXPECTED = {
    "color_space": "bt709",
    "color_primaries": "bt709",
    "color_transfer": "bt709",
    "color_range": "tv",
}
TESTSRC = "testsrc2=size=320x180:rate=30"


@dataclass
class ProbeResult:
    ok: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)


def parse_encoders(text: str) -> set[str]:
    """Encoder names from `ffmpeg -hide_banner -encoders` (rows after the ------ line)."""
    names: set[str] = set()
    started = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("------"):
            started = True
            continue
        if not started or not stripped:
            continue
        parts = stripped.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            names.add(parts[1])
    return names


def list_encoders(ffmpeg: str, runner: Runner = run) -> tuple[set[str], str | None]:
    result = runner([ffmpeg, "-hide_banner", "-encoders"], 30.0)
    if result.returncode != 0:
        return set(), f"`ffmpeg -encoders` failed: {result.tail()}"
    return parse_encoders(result.stdout), None


def encode_args(ffmpeg: str, encoder: str) -> list[str]:
    """A 1-second lavfi test encode to the null muxer."""
    args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error"]
    args += ["-f", "lavfi", "-i", TESTSRC, "-t", "1"]
    if encoder.endswith("_qsv"):
        args += ["-pix_fmt", "nv12"]
    return [*args, "-c:v", encoder, "-f", "null", "-"]


def try_encoder(ffmpeg: str, encoder: str, runner: Runner = run) -> ProbeResult:
    result = runner(encode_args(ffmpeg, encoder), 90.0)
    if result.returncode == 0 and not result.timed_out:
        return ProbeResult(True, "usable (1 s test encode ok)")
    reason = ffmpeg_error_summary(result.output, encoder) or f"exit {result.returncode}"
    return ProbeResult(False, f"unusable: {reason}", {"error_tail": result.tail(3, 300)})


def ffmpeg_error_summary(text: str, encoder: str) -> str | None:
    """The encoder's own first complaint (e.g. 'Cannot load nvcuda.dll'), without the
    '[enc @ 0x...]' prefix; falls back to the first non-empty line."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    own = [ln for ln in lines if ln.startswith(f"[{encoder} @")]
    pick = (own or lines or [None])[0]
    if pick is None:
        return None
    return re.sub(r"^(\[[^\]]*@ [0-9A-Fa-fx]+\]\s*)+", "", pick)[:160]


def color_tag_mismatches(stream: dict[str, Any]) -> list[str]:
    """Tags that differ from the BT.709 delivery expectation, as 'key=got (want)'."""
    return [
        f"{key}={stream.get(key, 'missing')} (want {want})"
        for key, want in BT709_EXPECTED.items()
        if stream.get(key) != want
    ]


def check_bt709(ffmpeg: str, ffprobe: str, workdir: Path, runner: Runner = run) -> ProbeResult:
    """Encode 10 frames through the delivery chain and confirm all four tags with ffprobe."""
    out = workdir / "bt709.mp4"
    args = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-y"]
    args += ["-f", "lavfi", "-i", TESTSRC, "-frames:v", "10", "-vf", BT709_VF]
    args += ["-c:v", "libx264", "-crf", "18", "-preset", "slow"]
    args += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"]
    args += ["-color_range", "tv", str(out)]
    result = runner(args, 120.0)
    if result.returncode != 0 or not out.is_file():
        return ProbeResult(False, f"encode failed: {result.tail(2, 160)}")
    probe = runner(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=color_space,color_primaries,color_transfer,color_range",
            "-of",
            "json",
            str(out),
        ],
        30.0,
    )
    try:
        stream = json.loads(probe.stdout)["streams"][0]
    except (ValueError, KeyError, IndexError):
        return ProbeResult(False, f"ffprobe output unreadable: {probe.tail(2, 160)}")
    bad = color_tag_mismatches(stream)
    tags = {k: stream.get(k) for k in BT709_EXPECTED}
    if bad:
        return ProbeResult(False, "tags wrong: " + ", ".join(bad), {"tags": tags})
    return ProbeResult(True, "bt709/bt709/bt709, range tv", {"tags": tags})


# ---------------------------------------------------------------- Godot

GODOT_PROJECT = """\
config_version=5

[application]
config/name="kinetic-doctor-probe"
run/main_scene="res://main.tscn"
config/use_custom_user_dir=true
config/custom_user_dir_name="kinetic"

[display]
window/size/viewport_width=320
window/size/viewport_height=180
window/size/window_width_override=160
window/size/window_height_override=90
window/stretch/mode="viewport"

[rendering]
renderer/rendering_method="forward_plus"
"""

GODOT_SCENE = """\
[gd_scene load_steps=2 format=3]

[ext_resource type="Script" path="res://main.gd" id="1"]

[node name="Main" type="Node3D"]
script = ExtResource("1")
"""

GODOT_SCRIPT = """\
extends Node3D

var box: MeshInstance3D


func _ready() -> void:
\tvar cam := Camera3D.new()
\tadd_child(cam)
\tcam.position = Vector3(0.0, 1.5, 4.0)
\tcam.look_at(Vector3.ZERO)
\tbox = MeshInstance3D.new()
\tbox.mesh = BoxMesh.new()
\tvar mat := StandardMaterial3D.new()
\tmat.albedo_color = Color(0.23, 0.48, 0.84)
\tbox.material_override = mat
\tadd_child(box)
\tvar light := DirectionalLight3D.new()
\tadd_child(light)
\tlight.rotation_degrees = Vector3(-50.0, 30.0, 0.0)


func _process(delta: float) -> void:
\tbox.rotate_y(delta * 2.0)
"""


def write_godot_probe(project: Path) -> None:
    project.mkdir(parents=True, exist_ok=True)
    (project / "project.godot").write_text(GODOT_PROJECT, encoding="utf-8", newline="\n")
    (project / "main.tscn").write_text(GODOT_SCENE, encoding="utf-8", newline="\n")
    (project / "main.gd").write_text(GODOT_SCRIPT, encoding="utf-8", newline="\n")


def verify_frames(
    frames: list[Path], count: int, size: tuple[int, int], min_bytes: int = 1024
) -> str | None:
    """None when `frames` are `count` PNGs of `size` and at least one is non-trivial."""
    if len(frames) != count:
        return f"expected {count} PNGs, found {len(frames)}"
    for frame in frames:
        got = png_size(frame)
        if got != size:
            return f"{frame.name}: size {got}, want {size[0]}x{size[1]}"
    if not any(f.stat().st_size > min_bytes for f in frames):
        return f"all frames <= {min_bytes} bytes (blank output?)"
    return None


def godot_render(godot: str, scratch: Path, runner: Runner = run) -> ProbeResult:
    """Movie Maker: 10 frames at 320x180, forward_plus. Never `--headless` (M0: crashes)."""
    project = Path(tempfile.mkdtemp(prefix="godot-", dir=scratch))
    try:
        write_godot_probe(project)
        out = project / "out"
        out.mkdir()
        args = [godot, "--path", str(project), "--rendering-method", "forward_plus"]
        args += ["--write-movie", str(out / "frame.png"), "--fixed-fps", "30"]
        args += ["--quit-after", "10"]
        result = runner(args, 120.0)
        frames = sorted(out.glob("*.png"))
        problem = verify_frames(frames, 10, (320, 180))
        data = {"frames": len(frames), "exit": result.returncode}
        if problem:
            tail = result.tail(2, 160)
            return ProbeResult(False, problem + (f"; godot: {tail}" if tail else ""), data)
        return ProbeResult(True, "10 frames at 320x180 (forward_plus, Movie Maker)", data)
    finally:
        shutil.rmtree(project, ignore_errors=True)


def blender_script(out: Path) -> str:
    return "; ".join(
        [
            "import bpy",
            "s = bpy.context.scene",
            "s.render.engine = 'BLENDER_EEVEE'",
            "s.render.resolution_x = 160",
            "s.render.resolution_y = 90",
            "s.render.resolution_percentage = 100",
            "s.render.image_settings.file_format = 'PNG'",
            f"s.render.filepath = {out.as_posix()!r}",
            "bpy.ops.render.render(write_still=True)",
        ]
    )


def blender_render(blender: str, scratch: Path, runner: Runner = run) -> ProbeResult:
    """Background EEVEE render of the factory scene, one frame at 160x90."""
    work = Path(tempfile.mkdtemp(prefix="blender-", dir=scratch))
    try:
        out = work / "probe.png"
        args = [blender, "-b", "--factory-startup", "--python-exit-code", "1"]
        args += ["--python-expr", blender_script(out)]
        result = runner(args, 300.0)
        size = png_size(out)
        if size != (160, 90):
            why = "no PNG written" if not out.exists() else f"size {size}"
            return ProbeResult(False, f"{why}; blender: {result.tail(2, 160)}")
        return ProbeResult(True, "EEVEE frame at 160x90", {"exit": result.returncode})
    finally:
        shutil.rmtree(work, ignore_errors=True)
