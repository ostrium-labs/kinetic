"""Godot adapter without running Godot: project generation, command line, output checks."""

from __future__ import annotations

import shutil
import struct
import sys
import zlib
from pathlib import Path

import pytest

from ostrium_kinetic.adapters.godot import (
    ERROR_FILE,
    RUNTIME_DIR,
    GodotAdapter,
    build_project,
    check_output,
    godot_command,
    render_timeout,
    runtime_sha256,
    set_project_settings,
    window_size,
)
from ostrium_kinetic.adapters.process import ProcessResult, run_logged
from ostrium_kinetic.job import AdapterError, ShotJob, frame_name


def _png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + ihdr
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    )


def _job(tmp_path: Path, film_dir: Path, **over: object) -> ShotJob:
    fields: dict[str, object] = {
        "film_id": "kinetic.test",
        "shot_id": "S1",
        "renderer": "godot",
        "film_dir": film_dir.as_posix(),
        "scene": "scenes/godot/shot.tscn",
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "frame_count": 3,
        "framing": "16:9",
        "camera": "wide",
        "quality": "high",
        "out_dir": (tmp_path / "run").as_posix(),
    }
    fields.update(over)
    return ShotJob.model_validate(fields)


@pytest.fixture
def film_dir(tmp_path: Path) -> Path:
    film = tmp_path / "film"
    scenes = film / "scenes" / "godot"
    (scenes / "lib").mkdir(parents=True)
    (scenes / "shot.tscn").write_text("[gd_scene format=3]\n", "utf-8")
    (scenes / "shot.gd").write_text("extends Node3D\n", "utf-8")
    (scenes / "lib" / "util.gd").write_text("extends RefCounted\n", "utf-8")
    (scenes / ".godot").mkdir()
    (scenes / ".godot" / "cache.bin").write_bytes(b"x")
    (film / "sim").mkdir()
    (film / "sim" / "model.py").write_text("x = 1\n", "utf-8")
    return film


def test_window_size_fits_640_box_and_keeps_aspect() -> None:
    assert window_size(1920, 1080) == (640, 360)
    assert window_size(1080, 1920) == (360, 640)
    assert window_size(320, 180) == (320, 180)  # never enlarged


def test_set_project_settings_replaces_and_merges_sections() -> None:
    text = (
        '; comment\nconfig_version=5\n\n[application]\n\nconfig/name="k"\n\n'
        "[display]\n\nwindow/size/viewport_width=1280\nwindow/old=1\n\n"
        '[rendering]\n\nrenderer/rendering_method="mobile"\nkeep/me=true\n'
    )
    out = set_project_settings(
        text,
        {
            "display": {"window/size/viewport_width": 320, "window/stretch/mode": "viewport"},
            "rendering": {"renderer/rendering_method": "forward_plus", "aa/use": False},
            "new": {"x": 1.5},
        },
        replace=["display"],
    )
    assert out.startswith("; comment\nconfig_version=5\n\n[application]\n")
    display = out.split("\n[display]\n")[1].split("[rendering]")[0]
    assert "window/size/viewport_width=320" in display
    assert 'window/stretch/mode="viewport"' in display
    assert "window/old" not in display
    rendering = out.split("[rendering]")[1].split("[new]")[0]
    assert 'renderer/rendering_method="forward_plus"' in rendering
    assert "keep/me=true" in rendering
    assert "aa/use=false" in rendering
    assert out.endswith("[new]\n\nx=1.5\n")
    assert 'config/name="k"' in out


def test_build_project_copies_runtime_and_scene_dir(tmp_path: Path, film_dir: Path) -> None:
    job = _job(tmp_path, film_dir, quality="high")
    project = build_project(job, tmp_path / "run")
    assert (project / "addons" / "kinetic_runtime" / "kinetic.gd").is_file()
    ws = project / "workspace" / "scenes" / "godot"
    assert (ws / "shot.tscn").is_file()
    assert (ws / "lib" / "util.gd").is_file()
    assert not (ws / ".godot").exists()
    assert not (project / "workspace" / "sim").exists()  # only the scene's directory
    assert not (project / ".godot").exists()

    config = (project / "project.godot").read_text("utf-8")
    display = config.split("\n[display]\n")[1].split("\n[")[0]
    assert "window/size/viewport_width=1920" in display
    assert "window/size/viewport_height=1080" in display
    assert "window/size/window_width_override=640" in display
    assert "window/size/window_height_override=360" in display
    assert 'window/stretch/mode="viewport"' in display
    assert "anti_aliasing/quality/msaa_3d=2" in config
    assert "anti_aliasing/quality/screen_space_aa=0" in config
    assert 'Kinetic="*res://addons/kinetic_runtime/kinetic.gd"' in config
    assert 'config/custom_user_dir_name="kinetic"' in config

    draft = build_project(_job(tmp_path, film_dir, quality="draft"), tmp_path / "run")
    assert "anti_aliasing/quality/msaa_3d=0" in (draft / "project.godot").read_text("utf-8")


def test_build_project_rejects_bad_scenes(tmp_path: Path, film_dir: Path) -> None:
    with pytest.raises(AdapterError, match="subdirectory"):
        build_project(_job(tmp_path, film_dir, scene="shot.tscn"), tmp_path / "run")
    with pytest.raises(AdapterError, match="scene not found"):
        build_project(_job(tmp_path, film_dir, scene="scenes/godot/nope.tscn"), tmp_path / "run")


def test_godot_command(tmp_path: Path, film_dir: Path) -> None:
    job = _job(tmp_path, film_dir, frame_count=105)
    run = tmp_path / "run"
    args = godot_command("godot.exe", run / "project", run, job)
    assert args == [
        "godot.exe",
        "--path",
        str(run / "project"),
        "--rendering-method",
        "forward_plus",
        "--write-movie",
        str(run / "frames" / "frame.png"),
        "--fixed-fps",
        "30",
        "--quit-after",
        "105",
        "--",
        "--kinetic-job",
        str(run / "job.json"),
    ]
    assert "--headless" not in args
    assert render_timeout(105) == 330.0


def test_runtime_sha256_is_stable_and_ignores_import_cache(tmp_path: Path) -> None:
    copy = tmp_path / "godot"
    shutil.copytree(RUNTIME_DIR, copy, ignore=shutil.ignore_patterns(".godot"))
    base = runtime_sha256(copy)
    assert base == runtime_sha256(copy)
    assert base == runtime_sha256(RUNTIME_DIR) == GodotAdapter().runtime_sha256()
    (copy / ".godot").mkdir()
    (copy / ".godot" / "uid_cache.bin").write_bytes(b"cache")
    assert runtime_sha256(copy) == base
    (copy / "addons" / "kinetic_runtime" / "kinetic.gd").write_text("changed\n", "utf-8")
    assert runtime_sha256(copy) != base


def _fake_run(tmp_path: Path, job: ShotJob, frames: int, size: tuple[int, int]) -> Path:
    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True, exist_ok=True)
    for i in range(frames):
        (run / "frames" / frame_name(i)).write_bytes(_png(*size))
    lines = [f'{{"frame":{i},"tick":{i},"entities":{{}},"labels":[]}}' for i in range(frames)]
    (run / "annotations.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    (run / "render.log").write_text("Godot Engine v4.7.2\n", "utf-8")
    return run


def _result(run: Path, code: int | None = 0, timed_out: bool = False) -> ProcessResult:
    return ProcessResult(code, 1.0, timed_out, run / "render.log")


def test_check_output_accepts_a_complete_run(tmp_path: Path, film_dir: Path) -> None:
    job = _job(tmp_path, film_dir, width=32, height=18)
    run = _fake_run(tmp_path, job, 3, (32, 18))
    check_output(job, run, _result(run))


@pytest.mark.parametrize(
    ("frames", "size", "code", "match"),
    [
        (2, (32, 18), 0, "expected 3 frames, found 2"),
        (3, (32, 20), 0, "expected \\(32, 18\\)"),
        (3, (32, 18), 1, "exited with code 1"),
    ],
)
def test_check_output_rejects_bad_runs(
    tmp_path: Path,
    film_dir: Path,
    frames: int,
    size: tuple[int, int],
    code: int,
    match: str,
) -> None:
    job = _job(tmp_path, film_dir, width=32, height=18)
    run = _fake_run(tmp_path, job, frames, size)
    with pytest.raises(AdapterError, match=match):
        check_output(job, run, _result(run, code))


def test_check_output_reports_runtime_errors_and_log_tail(tmp_path: Path, film_dir: Path) -> None:
    job = _job(tmp_path, film_dir, width=32, height=18)
    run = _fake_run(tmp_path, job, 3, (32, 18))
    (run / ERROR_FILE).write_text("shot scene has no Camera3D named 'wide'\n", "utf-8")
    with pytest.raises(AdapterError, match="no Camera3D named 'wide'") as info:
        check_output(job, run, _result(run, 3))
    assert "Godot Engine v4.7.2" in str(info.value)  # log tail included

    (run / ERROR_FILE).unlink()
    with (run / "render.log").open("a", encoding="utf-8") as fh:
        fh.write("SCRIPT ERROR: Invalid access to property\n")
    with pytest.raises(AdapterError, match="GDScript errors"):
        check_output(job, run, _result(run))

    with pytest.raises(AdapterError, match="timed out"):
        check_output(job, run, _result(run, None, timed_out=True))


def test_check_output_checks_annotations(tmp_path: Path, film_dir: Path) -> None:
    job = _job(tmp_path, film_dir, width=32, height=18)
    run = _fake_run(tmp_path, job, 3, (32, 18))
    (run / "annotations.jsonl").write_text('{"frame":0}\n{"frame":2}\n{"frame":1}\n', "utf-8")
    with pytest.raises(AdapterError, match="line 2 is for frame 2"):
        check_output(job, run, _result(run))
    (run / "annotations.jsonl").write_text('{"frame":0}\n', "utf-8")
    with pytest.raises(AdapterError, match="1 lines, expected 3"):
        check_output(job, run, _result(run))
    no_annotations = job.model_copy(update={"annotate": False})
    check_output(no_annotations, run, _result(run))


def test_render_rejects_mismatched_out_dir(tmp_path: Path, film_dir: Path) -> None:
    job = _job(tmp_path, film_dir)
    adapter = GodotAdapter(executable=tmp_path / "godot.exe")
    with pytest.raises(AdapterError, match="not the run directory"):
        adapter.render(job, tmp_path / "elsewhere", ws=None)  # type: ignore[arg-type]


def test_run_logged_captures_output_and_kills_on_timeout(tmp_path: Path) -> None:
    log = tmp_path / "out.log"
    ok = run_logged([sys.executable, "-c", "print('hello'); import sys; sys.exit(3)"], log, 60)
    assert ok.returncode == 3 and not ok.timed_out
    assert "hello" in log.read_text("utf-8")

    slow = run_logged([sys.executable, "-c", "import time; time.sleep(60)"], log, 1)
    assert slow.timed_out and slow.returncode is None
    assert slow.seconds < 30
    assert "killed after timeout" in slow.tail()
