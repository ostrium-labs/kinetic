from __future__ import annotations

import json
import struct
import zlib
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ostrium_kinetic.contract.models import Checkpoint, Expectation
from ostrium_kinetic.job import EntityAnnotation, FrameAnnotation, ShotJob, frame_name
from ostrium_kinetic.qa.annotations import check_annotations
from ostrium_kinetic.qa.expect import MISSING, matches, observed, span_view, trace_observed
from ostrium_kinetic.qa.frames import check_frames
from ostrium_kinetic.qa.report import QaCheck, QaReport, check
from ostrium_kinetic.qa.stills import keep_stills
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.sim.trace import Trace

W, H = 32, 18


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


def _frames(directory: Path, n: int, w: int = W, h: int = H) -> Path:
    rng = np.random.default_rng(0)
    for i in range(n):
        write_png(directory / frame_name(i), rng.integers(0, 255, (h, w, 3), dtype=np.uint8))
    return directory


def _by_id(checks: list[QaCheck]) -> dict[str, QaCheck]:
    return {c.id: c for c in checks}


# ---------------------------------------------------------------- report


def test_report_exit_code_summary_and_canonical_write(tmp_path: Path) -> None:
    report = QaReport(
        film_id="f",
        profile="preview",
        checks=[
            check("frames.count", "S1", "pass", "ok", found=3),
            check("media.black", None, "warn", "black: 0.00-1.00 s"),
        ],
    )
    assert report.exit_code == 0
    assert report.summary() == "QA PASS with warnings: 1 pass, 1 warn, 0 fail"
    report.checks.append(check("delivery.color", None, "fail", "color_trc=unknown"))
    assert report.exit_code == 1
    text = report.render_text()
    assert text.index("FAIL") < text.index("WARN") < text.index("PASS  S1")
    path = report.write(tmp_path / "qa" / "report.json")
    raw = path.read_bytes()
    assert raw.endswith(b"}\n") and b"\r\n" not in raw and b'", "' not in raw
    assert QaReport.load(path) == report
    assert json.loads(raw)["checks"][0]["data"] == {"found": 3}


# ---------------------------------------------------------------- frames


def test_frames_pass(tmp_path: Path) -> None:
    checks = check_frames(_frames(tmp_path / "frames", 5), 5, W, H, "S1")
    assert all(c.status == "pass" for c in checks), checks
    assert {c.id for c in checks} == {
        "frames.count",
        "frames.numbering",
        "frames.size",
        "frames.integrity",
    }


def test_frames_missing_extra_wrong_size_truncated(tmp_path: Path) -> None:
    d = _frames(tmp_path / "frames", 12)
    (d / frame_name(3)).unlink()
    (d / frame_name(7)).unlink()
    write_png(d / frame_name(12), np.zeros((H, W, 3), np.uint8))  # beyond frame_count
    write_png(d / frame_name(5), np.zeros((H + 2, W, 3), np.uint8))  # wrong size
    data = (d / frame_name(6)).read_bytes()
    (d / frame_name(6)).write_bytes(data[: len(data) // 2])  # truncated write
    (d / "frame.wav").write_bytes(b"RIFF")
    c = _by_id(check_frames(d, 12, W, H, "S1"))
    assert c["frames.count"].status == "fail"  # 11 PNGs for 12 frames
    numbering = c["frames.numbering"]
    assert numbering.status == "fail"
    assert numbering.data["missing"] == [frame_name(3), frame_name(7)]
    assert numbering.data["extra"] == [frame_name(12)]
    assert "frame00000003.png" in numbering.detail
    assert c["frames.size"].status == "fail" and frame_name(5) in c["frames.size"].detail
    assert c["frames.integrity"].status == "fail" and frame_name(6) in c["frames.integrity"].detail
    assert c["frames.stray_files"].status == "warn"


def test_frames_missing_directory(tmp_path: Path) -> None:
    (only,) = check_frames(tmp_path / "nope", 3, W, H, "S1")
    assert only.status == "fail"


def test_keep_stills(tmp_path: Path) -> None:
    d = _frames(tmp_path / "frames", 6)
    kept = keep_stills(d, tmp_path / "stills", [4, 0, 4])
    assert [p.name for p in kept] == [frame_name(0), frame_name(4)]
    assert kept[1].read_bytes() == (d / frame_name(4)).read_bytes()
    with pytest.raises(FileNotFoundError):
        keep_stills(d, tmp_path / "stills", [9])


# ---------------------------------------------------------------- expectations


def test_expect_semantics() -> None:
    span = {"span_id": "tx:1", "via": "link:P", "kind": "transit", "progress": 0.5}

    def exp(field: str, equals: Any = None) -> Expectation:
        return Expectation(entity="packet:1", field=field, equals=equals)

    assert observed(exp("status"), {"status": "lost"}, None) == "lost"
    assert observed(exp("status"), None, None) is MISSING
    assert observed(exp("active_span"), {}, None) is None
    assert observed(exp("active_span"), {}, span) == "tx:1"
    assert observed(exp("active_span.via"), {}, span) == "link:P"
    assert observed(exp("active_span.via"), {}, None) is MISSING
    assert matches(1, 1.0) and not matches(True, 1) and not matches(MISSING, None)


# ---------------------------------------------------------------- annotations


@pytest.fixture(scope="module")
def trace(
    tmp_path_factory: pytest.TempPathFactory, failover_module: Path, failover_params: dict
) -> Trace:
    ref = ensure_trace(failover_module, failover_params, 7, tmp_path_factory.mktemp("cache"))
    return Trace.load(ref.directory)


CHECKPOINTS = [
    Checkpoint(at_frame=30, expect=[Expectation(entity="packet:1", field="active_span.via",
                                                equals="link:P")]),
    Checkpoint(at_frame=40, expect=[Expectation(entity="link:P", field="status", equals="failed")]),
    Checkpoint(at_frame=60, expect=[Expectation(entity="packet:2", field="active_span.via",
                                                equals="link:S")]),
    Checkpoint(at_frame=90, expect=[Expectation(entity="packet:2", field="status",
                                                equals="delivered")]),
]  # fmt: skip
ENTITIES = ("link:P", "link:S", "packet:1", "packet:2")


def _job(framing: str = "16:9", width: int = 1280, height: int = 720) -> ShotJob:
    return ShotJob(
        film_id="kinetic.smoke-failover",
        shot_id="S1",
        renderer="godot",
        film_dir="C:/films/smoke",
        scene="scenes/godot/failover.tscn",
        fps=Fraction(30),
        width=width,
        height=height,
        frame_count=105,
        framing=framing,
        camera="wide" if framing == "16:9" else "tall",
        quality="draft",
        tick_rate=Fraction(1, 1000),
        tick_start=0,
        tick_end=3500,
        out_dir="C:/runs/S1",
    )


def _annotate(job: ShotJob, trace: Trace) -> list[FrameAnnotation]:
    """What a correct renderer would write: the trace, sampled at each frame's tick."""
    out = []
    label_y = 0.5 * job.height
    for f in range(job.frame_count):
        tick = job.tick_at(f)
        assert tick is not None
        states = trace.state_at(tick)
        entities = {}
        for e in ENTITIES:
            active = trace.active_span_of(e, tick)
            span = None
            if active is not None:
                span = {**span_view(active.span), "progress": float(active.progress)}
            entities[e] = EntityAnnotation(
                state=dict(states.get(e, {})),
                active_span=span,
                bbox=(10, 10, 50, 50) if e in states else None,
                visible=e in states,
            )
        labels = [("t = 1.2 s", (40.0, label_y, 240.0, label_y + 60 * job.height / 1080))]
        out.append(FrameAnnotation(frame=f, tick=tick, entities=entities, labels=labels))
    return out


def _write(path: Path, frames: list[FrameAnnotation]) -> Path:
    text = "".join(a.model_dump_json(exclude_none=True) + "\n" for a in frames)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_annotations_pass(tmp_path: Path, trace: Trace) -> None:
    job = _job()
    path = _write(tmp_path / "annotations.jsonl", _annotate(job, trace))
    checks = check_annotations(job, path, trace, CHECKPOINTS, "S1")
    assert [c for c in checks if c.status != "pass"] == []
    ids = [c.id for c in checks]
    assert ids.count("annotations.checkpoint") == 4
    assert "annotations.parity" in ids and "annotations.layout.safe_area" not in ids


def test_annotations_wrong_state_and_progress(tmp_path: Path, trace: Trace) -> None:
    job = _job()
    frames = _annotate(job, trace)
    frames[40].entities["link:P"].state["status"] = "up"  # renderer read stale state
    span = frames[30].entities["packet:1"].active_span
    assert span is not None
    span["progress"] = float(span["progress"] or 0) + 0.01  # eased progress
    path = _write(tmp_path / "a.jsonl", frames)
    c = check_annotations(job, path, trace, CHECKPOINTS, "S1")
    parity = next(x for x in c if x.id == "annotations.parity")
    assert parity.status == "fail" and parity.data["mismatch_count"] == 2
    assert "frame 30" in parity.detail and "progress" in parity.detail
    assert "frame 40" in parity.detail
    cps = {x.data["frame"]: x for x in c if x.id == "annotations.checkpoint"}
    assert cps[40].status == "fail" and "expected 'failed', got 'up'" in cps[40].detail
    assert cps[30].status == "pass"


def test_annotations_invisible_checkpoint_entity(tmp_path: Path, trace: Trace) -> None:
    job = _job()
    frames = _annotate(job, trace)
    frames[60].entities["packet:2"].visible = False
    path = _write(tmp_path / "a.jsonl", frames)
    cps = {
        x.data["frame"]: x
        for x in check_annotations(job, path, trace, CHECKPOINTS, "S1")
        if x.id == "annotations.checkpoint"
    }
    assert cps[60].status == "fail" and "shown nowhere on screen" in cps[60].detail


def test_annotations_structure_errors(tmp_path: Path, trace: Trace) -> None:
    job = _job()
    frames = _annotate(job, trace)
    frames[10].tick = 999
    path = _write(tmp_path / "a.jsonl", frames[:-1])
    c = _by_id(check_annotations(job, path, None, [], "S1"))
    assert c["annotations.structure"].status == "fail"
    assert "104 annotations" in c["annotations.structure"].detail
    assert any("frame 10: tick 999" in p for p in c["annotations.structure"].data["problems"])
    assert "annotations.parity" not in c  # no trace given
    (missing,) = check_annotations(job, tmp_path / "none.jsonl", trace, [], "S1")
    assert missing.status == "fail"


def test_annotations_layout_9x16(tmp_path: Path, trace: Trace) -> None:
    job = _job("9:16", 1080, 1920)
    frames = _annotate(job, trace)
    frames[5].labels = [("caption", (40.0, 1700.0, 600.0, 1760.0))]  # in the bottom 20%
    frames[6].labels = [("edge", (900.0, 900.0, 1200.0, 960.0))]  # off the right edge
    frames[7].labels = [("tiny", (40.0, 900.0, 300.0, 920.0))]  # 20 px < 28*1920/1080
    frames[8].labels = [(f"l{i}", (40.0, 600.0 + 60 * i, 300.0, 655.0 + 60 * i)) for i in range(5)]
    path = _write(tmp_path / "a.jsonl", frames)
    c = _by_id(check_annotations(job, path, trace, CHECKPOINTS, "S1"))
    assert c["annotations.layout.safe_area"].status == "fail"
    assert "caption" in c["annotations.layout.safe_area"].detail
    assert c["annotations.layout.bounds"].status == "fail"
    assert "edge" in c["annotations.layout.bounds"].detail
    assert c["annotations.layout.label_size"].status == "warn"
    assert "tiny" in c["annotations.layout.label_size"].detail
    assert c["annotations.layout.clutter"].status == "warn"
    assert "frame 8" in c["annotations.layout.clutter"].detail


def test_trace_observed_matches_validate_semantics(trace: Trace) -> None:
    job = _job()
    for cp in CHECKPOINTS:
        tick = job.tick_at(cp.at_frame)
        assert tick is not None
        for exp in cp.expect:
            assert matches(trace_observed(trace, exp, tick), exp.equals)


def test_annotations_layout_occlusion(tmp_path: Path, trace: Trace) -> None:
    from ostrium_kinetic.job import EntityAnnotation

    job = _job()
    frames = _annotate(job, trace)
    frames[3].entities["node:A"] = EntityAnnotation(visible=True, bbox=(100.0, 100.0, 200.0, 200.0))
    frames[3].labels = [("badge", (90.0, 120.0, 260.0, 160.0))]  # sits on node A
    frames[4].entities["node:A"] = EntityAnnotation(visible=True, bbox=(100.0, 100.0, 200.0, 200.0))
    frames[4].labels = [("clear", (300.0, 120.0, 420.0, 160.0))]  # beside it
    path = _write(tmp_path / "a.jsonl", frames)
    c = _by_id(check_annotations(job, path, trace, [], "S1"))
    occ = c["annotations.layout.occlusion"]
    assert occ.status == "warn"
    assert "badge" in occ.detail and "node:A" in occ.detail
    assert "clear" not in occ.detail


def test_clutter_rule_ignores_badge_and_time_label(tmp_path: Path, trace: Trace) -> None:
    job = _job().model_copy(update={"evidence": "simulated", "time_label": "time slowed 3.4x"})
    frames = _annotate(job, trace)
    box = (40.0, 100.0, 300.0, 140.0)
    chrome = [("SIMULATED · model", box), ("time slowed 3.4x", box)]
    frames[2].labels = chrome + [(f"l{i}", box) for i in range(4)]  # 4 content labels: fine
    frames[3].labels = chrome + [(f"l{i}", box) for i in range(5)]  # 5: clutter
    path = _write(tmp_path / "a.jsonl", frames)
    clutter = _by_id(check_annotations(job, path, trace, [], "S1"))["annotations.layout.clutter"]
    assert clutter.status == "warn"
    assert "frame 3" in clutter.detail and "frame 2" not in clutter.detail
