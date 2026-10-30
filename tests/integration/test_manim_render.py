"""Render the manim-trace-timeline example with Manim: exact frame count, RGB frames,
annotations against the trace and the film's checkpoints, and byte-identical reruns.

Skipped unless `manim` is importable (`uv sync --extra manim`); CI does not install it.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from ostrium_kinetic.adapters.manim import ERROR_FILE, MEDIA_DIR, ManimAdapter
from ostrium_kinetic.contract.loader import load_film
from ostrium_kinetic.job import ShotJob, frame_name
from ostrium_kinetic.qa.annotations import check_annotations
from ostrium_kinetic.qa.frames import check_frames
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.sim.trace import Trace
from ostrium_kinetic.workspace import Workspace

pytestmark = [
    pytest.mark.renderer("manim"),
    pytest.mark.skipif(
        importlib.util.find_spec("manim") is None,
        reason="manim not importable (uv sync --extra manim)",
    ),
]

TRACKED = ["packet:1", "packet:2", "packet:ack2"]
TIME_LABEL = "SLOWED 2\N{MULTIPLICATION SIGN}"  # as in film.yaml


@pytest.fixture(scope="module")
def setup(
    tmp_path_factory: pytest.TempPathFactory,
    repo_root: Path,
    failover_module: Path,
    failover_params: dict[str, Any],
) -> tuple[Workspace, Path, Trace, Path]:
    root = tmp_path_factory.mktemp("manim-render")
    (root / "kinetic.toml").write_text('[cache]\ndir = "cache"\n', "utf-8")
    ws = Workspace.find(root)
    film = repo_root / "examples" / "manim-trace-timeline"
    ref = ensure_trace(failover_module, failover_params, 7, ws.cache_dir)
    return ws, film, Trace.load(ref.directory), ref.directory


def make_job(film: Path, trace_dir: Path, run: Path, **over: Any) -> ShotJob:
    fields: dict[str, Any] = {
        "film_id": "kinetic.manim-trace-timeline",
        "shot_id": "T1",
        "renderer": "manim",
        "film_dir": film.as_posix(),
        "scene": "scenes/manim/timeline.py",
        "fps": 30,
        "width": 384,
        "height": 216,
        "frame_count": 210,
        "framing": "16:9",
        "camera": "wide",
        "quality": "draft",
        "trace_dir": trace_dir.as_posix(),
        "tick_rate": "1/1000",
        "tick_start": 0,
        "tick_end": 3500,
        "evidence": "simulated",
        "time_label": TIME_LABEL,
        "out_dir": run.as_posix(),
    }
    fields.update(over)
    return ShotJob.model_validate(fields)


@pytest.fixture(scope="module")
def rendered(
    setup: tuple[Workspace, Path, Trace, Path],
) -> tuple[ShotJob, Path, list[dict[str, Any]]]:
    ws, film, _, trace_dir = setup
    run = ws.cache("runs", "t1")
    job = make_job(film, trace_dir, run)
    result = ManimAdapter().render(job, run, ws)
    assert result.status == "frames_ok"
    assert result.renderer_version == "0.22.1"
    lines = (run / "annotations.jsonl").read_text("utf-8").splitlines()
    return job, run, [json.loads(line) for line in lines]


def _ihdr(path: Path) -> tuple[int, int, int, int]:
    data = path.read_bytes()[16:26]
    width, height = int.from_bytes(data[0:4]), int.from_bytes(data[4:8])
    return width, height, data[8], data[9]  # bit depth, colour type


def test_exact_frames_rgb_and_no_leftovers(
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    job, run, annotations = rendered
    frames = sorted(p.name for p in (run / "frames").iterdir())
    assert frames == [frame_name(i) for i in range(job.frame_count)]
    for name in (frames[0], frames[-1]):
        assert _ihdr(run / "frames" / name) == (384, 216, 8, 2)  # 8-bit RGB, no alpha
    assert len(annotations) == job.frame_count
    assert not (run / ERROR_FILE).exists() and not (run / MEDIA_DIR).exists()
    statuses = {c.id: c.status for c in check_frames(run / "frames", 210, 384, 216, "T1")}
    assert set(statuses.values()) == {"pass"}


def test_annotations_match_the_trace(
    setup: tuple[Workspace, Path, Trace, Path],
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    _, _, trace, _ = setup
    job, _, annotations = rendered
    for f, ann in enumerate(annotations):
        tick = job.tick_at(f)
        assert tick is not None and ann["frame"] == f and ann["tick"] == tick
        assert sorted(ann["entities"]) == TRACKED
        state = trace.state_at(tick)
        for entity in TRACKED:
            got = ann["entities"][entity]
            assert got["state"] == state.get(entity, {}), (f, entity)
            active = trace.active_span_of(entity, tick)
            if active is None:
                assert got["active_span"] is None
            else:
                assert got["active_span"]["span_id"] == active.span.span_id
                assert got["active_span"]["planned_progress"] == pytest.approx(
                    float(active.planned_progress), abs=1e-12
                )
            # A bar is drawn from the moment its span begins, and stays.
            began = any(s.entity == entity and s.begin_tick <= tick for s in trace.spans)
            assert got["visible"] == began, (f, entity)
        texts = [t for t, _ in ann["labels"]]
        assert texts[0] == f"SIMULATED · {TIME_LABEL}"
        assert f"{tick} ms" in texts


def test_bar_length_follows_the_tick(
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    """packet:1's bar grows linearly with the tick until the cut at 1200, then stops."""
    job, _, annotations = rendered

    def width(f: int) -> float:
        x0, _, x1, _ = annotations[f]["entities"]["packet:1"]["bbox"]
        return x1 - x0

    plot = 384 - 2 * 144 * 216 / 1080  # the scene's plot width in output px
    for f in (15, 30, 60):
        tick = job.tick_at(f)
        assert tick is not None
        assert width(f) == pytest.approx(plot * tick / 3500, abs=0.02)
    assert width(90) == pytest.approx(plot * 1200 / 3500, abs=0.02) == width(200)


def test_qa_annotation_checks_pass_with_the_film_checkpoints(
    setup: tuple[Workspace, Path, Trace, Path],
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    _, film, trace, _ = setup
    job, run, _ = rendered
    loaded = load_film(film)
    shot = next(s for _, s in loaded.film.shots() if s.id == "T1")
    checks = check_annotations(job, run / "annotations.jsonl", trace, shot.checkpoints, "T1")
    bad = [(c.id, c.detail) for c in checks if c.status != "pass"]
    assert not bad
    assert sum(c.id == "annotations.checkpoint" for c in checks) == 4


def test_two_renders_are_byte_identical(setup: tuple[Workspace, Path, Trace, Path]) -> None:
    ws, film, _, trace_dir = setup

    def digest(name: str) -> list[str]:
        run = ws.cache("runs", name)
        job = make_job(film, trace_dir, run, frame_count=24, width=256, height=144)
        ManimAdapter().render(job, run, ws)
        files = [run / "frames" / frame_name(i) for i in range(24)]
        files.append(run / "annotations.jsonl")
        return [hashlib.sha256(p.read_bytes()).hexdigest() for p in files]

    assert digest("det-a") == digest("det-b")
