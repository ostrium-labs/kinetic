"""Semantic QA from annotations.jsonl: what the renderer says it drew, frame by frame.

Four groups of checks per shot:
  structure    one annotation per frame, in order, with the job's tick for that frame
  parity       annotated state and active spans equal the trace's at that tick
  checkpoints  the film's checkpoints hold on the annotated data, and their entities are visible
  layout       labels inside the frame (and the 9:16 safe area), legible, not cluttered
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ostrium_kinetic.contract.models import Checkpoint
from ostrium_kinetic.job import EntityAnnotation, FrameAnnotation, ShotJob
from ostrium_kinetic.qa.expect import matches, observed, show
from ostrium_kinetic.qa.report import QaCheck, check
from ostrium_kinetic.sim.trace import Trace

PROGRESS_TOLERANCE = 1e-6
SAFE_TOP = 0.14  # 9:16: top 14% reserved (platform UI)
SAFE_BOTTOM = 0.20  # 9:16: bottom 20% reserved (captions, platform UI)
MIN_LABEL_PX_1080 = 28  # minimum label height at 1080 px frame height
MAX_LABELS = 4  # HUD clutter rule
SHOW = 5


def _first(items: list[str]) -> str:
    more = f" (+{len(items) - SHOW} more)" if len(items) > SHOW else ""
    return "; ".join(items[:SHOW]) + more


def load_annotations(path: Path) -> tuple[list[FrameAnnotation], list[str]]:
    """Parsed lines and per-line errors ("line 3: ...")."""
    frames: list[FrameAnnotation] = []
    errors: list[str] = []
    with path.open(encoding="utf-8") as fh:
        for n, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                frames.append(FrameAnnotation.model_validate_json(line))
            except ValidationError as exc:
                first = exc.errors()[0]
                loc = ".".join(str(p) for p in first["loc"])
                errors.append(f"line {n}: {loc}: {first['msg']}")
    return frames, errors


def _structure(
    job: ShotJob, frames: list[FrameAnnotation], errors: list[str], shot_id: str
) -> QaCheck:
    problems = list(errors)
    if len(frames) != job.frame_count:
        problems.insert(0, f"{len(frames)} annotations, expected {job.frame_count}")
    for i, ann in enumerate(frames):
        if ann.frame != i:
            problems.append(f"line for frame {ann.frame} at position {i}")
        elif i < job.frame_count and ann.tick != job.tick_at(i):
            problems.append(f"frame {i}: tick {ann.tick}, expected {job.tick_at(i)}")
    return check(
        "annotations.structure",
        shot_id,
        "fail" if problems else "pass",
        _first(problems) if problems else f"{len(frames)} frames in order with job ticks",
        problems=problems[:50],
    )


def _span_mismatch(ann: dict[str, Any] | None, exact: Any) -> str | None:
    """None when the annotated span agrees with the trace's active span (or both are absent)."""
    if exact is None:
        return None if not ann else f"active_span {ann.get('span_id')!r}, trace has none"
    if not ann:
        return f"no active_span, trace has {exact.span.span_id!r}"
    if ann.get("span_id") != exact.span.span_id:
        return f"active_span {ann.get('span_id')!r}, trace has {exact.span.span_id!r}"
    progress = ann.get("progress")
    if isinstance(progress, bool) or not isinstance(progress, int | float):
        return f"active_span {exact.span.span_id!r} has no numeric progress"
    if abs(float(progress) - float(exact.progress)) > PROGRESS_TOLERANCE:
        return f"progress {progress} vs exact {exact.progress} ({float(exact.progress):.9f})"
    return None


def _state_equal(annotated: dict[str, Any], expected: dict[str, Any]) -> bool:
    if annotated.keys() != expected.keys():
        return False
    return all(matches(annotated[k], v) for k, v in expected.items())


def _parity(
    job: ShotJob, by_frame: dict[int, FrameAnnotation], trace: Trace, shot_id: str
) -> QaCheck:
    if job.tick_start is None or job.tick_end is None:
        return check("annotations.parity", shot_id, "fail", "job has no tick range for a trace")
    mismatches: list[str] = []
    compared = 0
    for frame in range(job.frame_count):
        ann = by_frame.get(frame)
        if ann is None or not ann.entities:
            continue
        tick = job.tick_at(frame)
        assert tick is not None
        states = trace.state_at(tick)
        for entity, ent in sorted(ann.entities.items()):
            compared += 1
            where = f"frame {frame} tick {tick} {entity}"
            expected = states.get(entity)
            if expected is None:
                if ent.state:
                    mismatches.append(f"{where}: annotated state but entity is not alive")
            elif not _state_equal(ent.state, expected):
                mismatches.append(f"{where}: state {ent.state} != trace {expected}")
            try:
                exact = trace.active_span_of(entity, tick)
            except ValueError as exc:
                mismatches.append(f"{where}: {exc}")
                continue
            problem = _span_mismatch(ent.active_span, exact)
            if problem:
                mismatches.append(f"{where}: {problem}")
    return check(
        "annotations.parity",
        shot_id,
        "fail" if mismatches else "pass",
        f"{len(mismatches)} mismatches with the trace: {_first(mismatches)}"
        if mismatches
        else f"{compared} entity-frames match the trace",
        mismatches=mismatches[:50],
        mismatch_count=len(mismatches),
    )


def _checkpoint(cp: Checkpoint, ann: FrameAnnotation | None, shot_id: str) -> QaCheck:
    frame = cp.at_frame
    if ann is None:
        return check(
            "annotations.checkpoint", shot_id, "fail", f"frame {frame}: no annotation", frame=frame
        )
    problems: list[str] = []
    for exp in cp.expect:
        ent: EntityAnnotation | None = ann.entities.get(exp.entity)
        if ent is None:
            problems.append(f"{exp.entity} {exp.field}: entity not annotated")
            continue
        state = ent.state or None  # {} means "not alive"
        span = ent.active_span or None
        actual = observed(exp, state, span)
        if not matches(actual, exp.equals):
            problems.append(f"{exp.entity} {exp.field} expected {exp.equals!r}, got {show(actual)}")
    for entity in dict.fromkeys(exp.entity for exp in cp.expect):
        ent = ann.entities.get(entity)
        if ent is not None and not ent.visible:
            problems.append(f"{entity} is shown nowhere on screen")
    return check(
        "annotations.checkpoint",
        shot_id,
        "fail" if problems else "pass",
        f"frame {frame}: " + ("; ".join(problems) if problems else f"{len(cp.expect)} hold"),
        frame=frame,
        problems=problems,
    )


def _is_chrome(text: str, job: ShotJob) -> bool:
    """The evidence badge and the time-scale label are standing chrome required by the
    evidence policy, not explanatory labels, so the clutter rule does not count them."""
    if job.time_label and job.time_label in text:
        return True
    return bool(job.evidence) and text.strip().upper().startswith(str(job.evidence).upper())


OCCLUSION_SHARE = 0.10  # share of the smaller rectangle; above this a label hides an entity
# A bbox only approximates compact things (nodes, packets). A long or curved entity (a link)
# has a box much larger than its pixels, so occlusion is checked only against entities whose
# box is at most this many times the label's area.
OCCLUSION_MAX_AREA_RATIO = 4.0


def _overlap_share(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    """Intersection area as a share of the smaller of two (x0, y0, x1, y1) rectangles."""
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (w * h) / smaller if smaller > 0 else 0.0


def _layout(job: ShotJob, frames: list[FrameAnnotation], shot_id: str) -> list[QaCheck]:
    w, h = job.width, job.height
    tall = job.framing == "9:16"
    top, bottom = SAFE_TOP * h, (1 - SAFE_BOTTOM) * h
    min_h = MIN_LABEL_PX_1080 * h / 1080
    outside: list[str] = []
    unsafe: list[str] = []
    small: list[str] = []
    cluttered: list[str] = []
    occluding: list[str] = []
    labels = 0
    for ann in frames:
        boxes = [(eid, e.bbox) for eid, e in ann.entities.items() if e.visible and e.bbox]
        content = [t for t, _ in ann.labels if not _is_chrome(t, job)]
        if len(content) > MAX_LABELS:
            cluttered.append(f"frame {ann.frame}: {len(content)} labels")
        for text, (x0, y0, x1, y1) in ann.labels:
            labels += 1
            where = f"frame {ann.frame} {text!r} [{x0:g},{y0:g},{x1:g},{y1:g}]"
            if x0 < 0 or y0 < 0 or x1 > w or y1 > h or x1 < x0 or y1 < y0:
                outside.append(where)
            elif tall and (y0 < top or y1 > bottom):
                unsafe.append(where)
            if y1 - y0 < min_h:
                small.append(f"{where} height {y1 - y0:g} px")
            label_area = max((x1 - x0) * (y1 - y0), 1.0)
            for eid, box in boxes:
                bx0, by0, bx1, by1 = box
                if (bx1 - bx0) * (by1 - by0) > OCCLUSION_MAX_AREA_RATIO * label_area:
                    continue  # too large for a bbox to stand in for its pixels
                if _overlap_share((x0, y0, x1, y1), box) > OCCLUSION_SHARE:
                    occluding.append(f"{where} covers {eid}")
    out = [
        check(
            "annotations.layout.bounds",
            shot_id,
            "fail" if outside else "pass",
            f"{len(outside)} labels leave the frame: {_first(outside)}"
            if outside
            else f"{labels} labels inside {w}x{h}",
            labels=outside[:50],
        ),
        check(
            "annotations.layout.label_size",
            shot_id,
            "warn" if small else "pass",
            f"{len(small)} labels under {min_h:g} px: {_first(small)}"
            if small
            else f"labels >= {min_h:g} px high",
            labels=small[:50],
        ),
        check(
            "annotations.layout.occlusion",
            shot_id,
            "warn" if occluding else "pass",
            f"{len(occluding)} label placements cover a tracked entity: {_first(occluding)}"
            if occluding
            else "no label covers a tracked entity",
            labels=occluding[:50],
        ),
        check(
            "annotations.layout.clutter",
            shot_id,
            "warn" if cluttered else "pass",
            f"more than {MAX_LABELS} labels (badge and time label excluded) "
            f"on {len(cluttered)} frames: {_first(cluttered)}"
            if cluttered
            else f"at most {MAX_LABELS} labels per frame",
            frames=cluttered[:50],
        ),
    ]
    if tall:
        out.insert(
            1,
            check(
                "annotations.layout.safe_area",
                shot_id,
                "fail" if unsafe else "pass",
                f"{len(unsafe)} labels outside the 9:16 safe area "
                f"(y {top:g}..{bottom:g}): {_first(unsafe)}"
                if unsafe
                else "labels inside the 9:16 safe area",
                labels=unsafe[:50],
            ),
        )
    return out


def check_annotations(
    job: ShotJob,
    annotations_path: Path,
    trace: Trace | None,
    checkpoints: list[Checkpoint],
    shot_id: str,
) -> list[QaCheck]:
    """All annotation checks for one shot. `trace` None skips parity (illustrative shots)."""
    if not annotations_path.is_file():
        return [check("annotations.structure", shot_id, "fail", f"{annotations_path} not found")]
    frames, errors = load_annotations(annotations_path)
    out = [_structure(job, frames, errors, shot_id)]
    by_frame: dict[int, FrameAnnotation] = {}
    for ann in frames:
        by_frame.setdefault(ann.frame, ann)
    if trace is not None:
        out.append(_parity(job, by_frame, trace, shot_id))
    out += [_checkpoint(cp, by_frame.get(cp.at_frame), shot_id) for cp in checkpoints]
    out += _layout(job, frames, shot_id)
    return out
