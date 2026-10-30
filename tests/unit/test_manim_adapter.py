"""Manim adapter and runtime without rendering: command line, output checks, the missing-manim
error, and the manim-free `Kinetic` sampler/annotation object against the reference trace."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ostrium_kinetic.adapters import get_adapter
from ostrium_kinetic.adapters.godot import ProcessResult
from ostrium_kinetic.adapters.manim import (
    ERROR_FILE,
    INSTALL_HINT,
    MEDIA_DIR,
    RUNTIME_DIR,
    RUNTIME_MODULE,
    ManimAdapter,
    check_output,
    manim_command,
    render_timeout,
    runtime_sha256,
)
from ostrium_kinetic.job import AdapterError, FrameAnnotation, ShotJob, frame_name
from ostrium_kinetic.manim_runtime.kinetic import Kinetic, annotation_line
from ostrium_kinetic.pipeline import adapter_for
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.sim.trace import Trace, stopped_progress


def _png(width: int, height: int) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + ihdr
        + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr))
    )


def _job(tmp_path: Path, **over: object) -> ShotJob:
    film = tmp_path / "film"
    (film / "scenes" / "manim").mkdir(parents=True, exist_ok=True)
    (film / "scenes" / "manim" / "shot.py").write_text("x = 1\n", "utf-8")
    fields: dict[str, object] = {
        "film_id": "kinetic.test",
        "shot_id": "S1",
        "renderer": "manim",
        "film_dir": film.as_posix(),
        "scene": "scenes/manim/shot.py",
        "fps": 30,
        "width": 32,
        "height": 18,
        "frame_count": 3,
        "framing": "16:9",
        "camera": "wide",
        "quality": "draft",
        "out_dir": (tmp_path / "run").as_posix(),
    }
    fields.update(over)
    return ShotJob.model_validate(fields)


def _fake_run(tmp_path: Path, frames: int, size: tuple[int, int]) -> Path:
    run = tmp_path / "run"
    (run / "frames").mkdir(parents=True, exist_ok=True)
    for i in range(frames):
        (run / "frames" / frame_name(i)).write_bytes(_png(*size))
    lines = [f'{{"frame":{i},"tick":null,"entities":{{}},"labels":[]}}' for i in range(frames)]
    (run / "annotations.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    (run / "render.log").write_text("$ python -m ostrium_kinetic.manim_runtime.run\n", "utf-8")
    return run


def _result(run: Path, code: int | None = 0, timed_out: bool = False) -> ProcessResult:
    return ProcessResult(code, 1.0, timed_out, run / "render.log")


def test_registered_with_pipeline_and_adapters() -> None:
    assert isinstance(adapter_for("manim"), ManimAdapter)
    assert isinstance(get_adapter("manim"), ManimAdapter)


def test_manim_command_uses_the_interpreter_and_runtime_module(tmp_path: Path) -> None:
    cmd = manim_command(["py", "-X", "utf8"], tmp_path)
    assert cmd == [
        "py",
        "-X",
        "utf8",
        "-m",
        RUNTIME_MODULE,
        "--kinetic-job",
        str(tmp_path / "job.json"),
    ]
    assert ManimAdapter().python == [sys.executable]


def test_render_timeout_scales_with_frames_and_pixels() -> None:
    assert render_timeout(210, 1920, 1080) > render_timeout(210, 1280, 720) > 120
    assert render_timeout(10, 320, 180) == pytest.approx(120 + 10 * 0.25)


def test_runtime_sha256_covers_runtime_python_only(tmp_path: Path) -> None:
    root = tmp_path / "rt"
    root.mkdir()
    (root / "scene.py").write_text("x = 1\n", "utf-8")
    first = runtime_sha256(root)
    (root / "notes.txt").write_text("ignored\n", "utf-8")
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "scene.cpython-312.pyc").write_bytes(b"\0")
    assert runtime_sha256(root) == first
    (root / "scene.py").write_text("x = 2\n", "utf-8")
    assert runtime_sha256(root) != first
    assert {p.name for p in RUNTIME_DIR.glob("*.py")} >= {"kinetic.py", "scene.py", "run.py"}
    assert len(ManimAdapter().runtime_sha256()) == 64


def test_check_output_accepts_a_complete_run(tmp_path: Path) -> None:
    job = _job(tmp_path)
    run = _fake_run(tmp_path, 3, (32, 18))
    check_output(job, run, _result(run))


@pytest.mark.parametrize(
    ("frames", "size", "code", "match"),
    [
        (2, (32, 18), 0, "expected 3 frames, found 2"),
        (4, (32, 18), 0, "unexpected \\['frame00000003.png'\\]"),
        (3, (32, 20), 0, "expected \\(32, 18\\)"),
        (3, (32, 18), 1, "exited with code 1"),
    ],
)
def test_check_output_rejects_bad_runs(
    tmp_path: Path, frames: int, size: tuple[int, int], code: int, match: str
) -> None:
    job = _job(tmp_path)
    run = _fake_run(tmp_path, frames, size)
    with pytest.raises(AdapterError, match=match):
        check_output(job, run, _result(run, code))


def test_check_output_reports_runtime_errors_with_log_tail(tmp_path: Path) -> None:
    job = _job(tmp_path)
    run = _fake_run(tmp_path, 3, (32, 18))
    (run / ERROR_FILE).write_text("font 'Nope Sans' is not installed\n", "utf-8")
    with pytest.raises(AdapterError, match="Nope Sans") as info:
        check_output(job, run, _result(run, 3))
    assert "manim_runtime.run" in str(info.value)  # log tail included
    (run / ERROR_FILE).unlink()
    with pytest.raises(AdapterError, match="timed out"):
        check_output(job, run, _result(run, None, timed_out=True))
    (run / MEDIA_DIR).mkdir()
    with pytest.raises(AdapterError, match="left behind"):
        check_output(job, run, _result(run))


def test_check_output_checks_annotation_frames_and_ticks(tmp_path: Path) -> None:
    job = _job(tmp_path)
    run = _fake_run(tmp_path, 3, (32, 18))
    (run / "annotations.jsonl").write_text('{"frame":0}\n{"frame":2}\n{"frame":1}\n', "utf-8")
    with pytest.raises(AdapterError, match="line 2 is for frame 2"):
        check_output(job, run, _result(run))
    timed = _job(tmp_path, tick_start=0, tick_end=300, tick_rate="1/1000")
    (run / "annotations.jsonl").write_text(
        '{"frame":0,"tick":0}\n{"frame":1,"tick":99}\n{"frame":2,"tick":200}\n', "utf-8"
    )
    with pytest.raises(AdapterError, match="frame 1: tick 99, expected 100"):
        check_output(timed, run, _result(run))
    check_output(job.model_copy(update={"annotate": False}), run, _result(run))


def test_render_rejects_wrong_jobs(tmp_path: Path) -> None:
    adapter = ManimAdapter(python=[sys.executable])
    with pytest.raises(AdapterError, match="not manim"):
        adapter.render(_job(tmp_path, renderer="godot"), tmp_path / "run", ws=None)  # type: ignore[arg-type]
    with pytest.raises(AdapterError, match="not the run directory"):
        adapter.render(_job(tmp_path), tmp_path / "elsewhere", ws=None)  # type: ignore[arg-type]
    with pytest.raises(AdapterError, match="scene not found"):
        adapter.render(_job(tmp_path, scene="scenes/manim/missing.py"), tmp_path / "run", ws=None)  # type: ignore[arg-type]


def test_missing_manim_is_a_clear_error(tmp_path: Path) -> None:
    """`python -S` skips site-packages, so this interpreter cannot import manim: the adapter
    must say how to install it, before starting a render."""
    adapter = ManimAdapter(python=[sys.executable, "-S"])
    assert adapter.version(None) is None  # type: ignore[arg-type]
    with pytest.raises(AdapterError, match="uv sync --extra manim") as info:
        adapter.render(_job(tmp_path), tmp_path / "run", ws=None)  # type: ignore[arg-type]
    assert "No module named 'manim'" in str(info.value)
    assert INSTALL_HINT in str(info.value)
    assert not (tmp_path / "run" / "job.json").exists()


def test_unstartable_interpreter_is_an_adapter_error(tmp_path: Path) -> None:
    adapter = ManimAdapter(python=[str(tmp_path / "no-such-python.exe")])
    assert adapter.version(None) is None  # type: ignore[arg-type]
    with pytest.raises(AdapterError, match="cannot start"):
        adapter.render(_job(tmp_path), tmp_path / "run", ws=None)  # type: ignore[arg-type]


# ------------------------------------------------------------------ runtime sampler (no manim)


class FakeMob:
    """Duck-typed Mobject: points and opacities, a family of one."""

    def __init__(self, points: list[tuple[float, float]], opacity: float = 1.0) -> None:
        self.points = np.array([[x, y, 0.0] for x, y in points])
        self.stroke_width = 0
        self._opacity = opacity

    def get_family(self) -> list[Any]:
        return [self]

    def get_fill_opacity(self) -> float:
        return self._opacity

    def get_stroke_opacity(self) -> float:
        return 0.0


@pytest.fixture(scope="module")
def failover_trace(
    tmp_path_factory: pytest.TempPathFactory,
    failover_module: Path,
    failover_params: dict[str, Any],
) -> Trace:
    cache = tmp_path_factory.mktemp("manim-trace")
    ref = ensure_trace(failover_module, failover_params, 7, cache)
    return Trace.load(ref.directory)


def _kinetic(trace: Trace | None, **over: Any) -> Kinetic:
    job: dict[str, Any] = {
        "frame_count": 105,
        "width": 1280,
        "height": 720,
        "camera": "wide",
        "framing": "16:9",
        "evidence": "simulated",
        "time_label": None,
        "tick_start": 0,
        "tick_end": 3500,
        "style": {"accent": "#58A6FF"},
    }
    job.update(over)
    return Kinetic(job, trace, 8.0 * 1280 / 720, 8.0)


def test_kinetic_samples_like_the_reference(failover_trace: Trace) -> None:
    k = _kinetic(failover_trace)
    for frame in range(105):
        k.set_frame(frame)
        tick = (3500 * frame) // 105
        assert k.tick == tick
        assert k.sim_seconds == pytest.approx(tick / 1000)
        assert k.shot_progress == frame / 105
        states = failover_trace.state_at(tick)
        for entity in ("packet:1", "packet:2", "packet:ack2", "link:P"):
            assert k.state(entity) == states.get(entity, {})
            assert k.alive(entity) == (entity in states)
            exact = failover_trace.active_span_of(entity, tick)
            view = k.active_span(entity)
            if exact is None:
                assert view == {}
                continue
            assert view["span_id"] == exact.span.span_id
            assert view["via"] == exact.span.attrs["via"]
            assert view["progress"] == float(exact.progress)
            assert view["planned_progress"] == float(exact.planned_progress)
            assert view["planned_end_tick"] == exact.span.planned_end_tick
        assert len(k.spans_active()) == len(failover_trace.spans_active(tick))
    assert k.evidence == "simulated" and k.time_label == ""
    assert k.style("accent") == "#58A6FF" and k.style("missing", 3) == 3


def test_kinetic_last_span_and_signals(failover_trace: Trace) -> None:
    k = _kinetic(failover_trace)
    k.set_frame(45)  # tick 1500: packet 1 was cut at 1200
    last = k.last_span("packet:1")
    span = failover_trace.last_span_of("packet:1", 1500)
    assert span is not None
    assert last["outcome"] == "lost" and last["progress"] == 1.0
    assert last["planned_progress"] == float(stopped_progress(span)) == 1200 / 1488
    assert k.last_span("packet:2") == {}
    names = [s["name"] for s in k.signals_in(0, 1500)]
    assert names == ["fault.injected", "timeout.fired"]
    assert k.signals_in(1500, 1600)[0]["attrs"] == {"via": "link:S"}


def test_kinetic_without_trace_has_no_clock() -> None:
    k = _kinetic(None, frame_count=3)
    k.set_frame(2)
    assert k.tick == -1 and k.state("packet:1") == {} and k.signals_in(0, 10) == []
    record = k.annotation(set())
    assert record["tick"] is None
    with pytest.raises(ValueError, match="outside shot"):
        k.set_frame(3)


def test_annotation_boxes_visibility_and_labels(failover_trace: Trace) -> None:
    k = _kinetic(failover_trace)
    s = k.px(1)  # 720p: 1 px@1080 = 2/3 output px
    assert s == pytest.approx(8.0 / 1080)
    assert k.to_pixels(0.0, 0.0) == pytest.approx((640.0, 360.0))
    bar = FakeMob([(-1.0, 0.5), (1.0, 1.0)])
    hidden = FakeMob([(0.0, 0.0), (1.0, 1.0)], opacity=0.0)
    offscreen = FakeMob([(50.0, 0.0), (51.0, 1.0)])
    not_added = FakeMob([(0.0, 0.0), (1.0, 1.0)])
    badge = FakeMob([(-7.0, 3.5), (-5.0, 4.2)])  # pokes above the frame
    k.track("packet:1", bar)
    k.track("packet:2", hidden)
    k.track("packet:ack2", offscreen)
    k.track("link:P", not_added)
    k.label("SIMULATED", badge)  # outside frame(): persistent
    frame_label = FakeMob([(0.0, -1.0), (1.0, -0.5)])
    k.run_frame(30, lambda: k.label("1000 ms", frame_label))
    on_screen = {id(m) for m in (bar, hidden, offscreen, badge, frame_label)}
    record = k.annotation(on_screen)
    FrameAnnotation.model_validate_json(annotation_line(record))  # the job.py schema
    ents = record["entities"]
    x0, y0 = 640 - 90, 360 - 90
    assert ents["packet:1"]["bbox"] == pytest.approx([x0, y0, 640 + 90, 360 - 45])
    assert ents["packet:1"]["visible"]
    assert ents["packet:1"]["active_span"]["span_id"] == "tx:packet:1"
    assert ents["packet:1"]["state"]["status"] == "in_flight"
    for hidden_entity in ("packet:2", "packet:ack2", "link:P"):
        assert ents[hidden_entity]["visible"] is False and ents[hidden_entity]["bbox"] is None
    assert [t for t, _ in record["labels"]] == ["SIMULATED", "1000 ms"]
    assert record["labels"][0][1][1] < 0  # labels are not clipped: QA checks the bounds
    k.run_frame(31, lambda: None)
    assert [t for t, _ in k.annotation(on_screen)["labels"]] == ["SIMULATED"]


def test_kinetic_rejects_a_bad_clock(failover_trace: Trace) -> None:
    with pytest.raises(ValueError, match="tick_start < tick_end"):
        _kinetic(failover_trace, tick_end=0)
