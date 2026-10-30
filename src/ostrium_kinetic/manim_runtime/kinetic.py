"""`Kinetic`: the per-frame view of the job and the trace that a Manim shot scene reads as
`self.k`, plus the annotation writer. The same members and dictionaries as the Godot runtime
(docs/runtime-api.md); sampling is `ostrium_kinetic.sim.trace`, the reference sampler.

This module does not import Manim. Tracked objects only need the Mobject methods used in
`screen_box` (duck-typed), so it can be unit-tested without the `manim` extra.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol

from ostrium_kinetic.sim.model import EventType, Span
from ostrium_kinetic.sim.trace import ActiveSpan, Trace, stopped_progress

REFERENCE_HEIGHT = 1080  # `px()` units: pixels on a frame 1080 px high (QA's reference)


class Boxed(Protocol):
    """What `track` and `label` need from a Mobject."""

    def get_family(self) -> list[Any]: ...


Box = tuple[float, float, float, float]


class RuntimeFailure(Exception):
    """A runtime or scene error carrying the subprocess exit code (see run.py)."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def tick_at(tick_start: int, tick_end: int, frame: int, frame_count: int) -> int:
    """The exact frame -> tick mapping of ShotJob.tick_at (never accumulated)."""
    return tick_start + ((tick_end - tick_start) * frame) // frame_count


def span_view(span: Span, progress: float, planned: float) -> dict[str, Any]:
    """{span_id, kind, interp, outcome, begin_tick, end_tick, progress, planned_progress} plus
    planned_end_tick when the span has one, plus the span's attrs; span fields win."""
    view: dict[str, Any] = dict(span.attrs)
    view.update(
        span_id=span.span_id,
        kind=span.kind,
        interp=span.interp,
        outcome=str(span.outcome),
        begin_tick=span.begin_tick,
        end_tick=span.end_tick,
    )
    if span.planned_end_tick is not None:
        view["planned_end_tick"] = span.planned_end_tick
    view["progress"] = progress
    view["planned_progress"] = planned
    return view


def _active_view(active: ActiveSpan) -> dict[str, Any]:
    return span_view(active.span, float(active.progress), float(active.planned_progress))


def _rounded(box: Iterable[float]) -> Box:
    x0, y0, x1, y1 = (round(float(v), 2) for v in box)
    return (x0, y0, x1, y1)


class Kinetic:
    """Job, clock and sampled trace for the current frame. `set_frame` is called by the
    runtime before the scene's `frame()`; scenes only read."""

    def __init__(
        self,
        job: dict[str, Any],
        trace: Trace | None,
        frame_width: float,
        frame_height: float,
        frame_center: tuple[float, float] = (0.0, 0.0),
    ) -> None:
        self.job = job
        self.trace = trace
        self.frame_count: int = int(job["frame_count"])
        self.width: int = int(job["width"])
        self.height: int = int(job["height"])
        self.camera: str = str(job.get("camera") or "")
        self.framing: str = str(job.get("framing") or "")
        self.calm: bool = bool(job.get("variant_calm", False))
        self.evidence: str = str(job["evidence"]) if job.get("evidence") is not None else ""
        self.time_label: str = str(job["time_label"]) if job.get("time_label") is not None else ""
        self.frame_width = frame_width  # scene units
        self.frame_height = frame_height
        self.frame_center = frame_center
        self.has_clock = trace is not None
        if trace is not None:
            ts, te = job.get("tick_start"), job.get("tick_end")
            if not (isinstance(ts, int) and isinstance(te, int) and te > ts):
                raise ValueError("job with a trace needs integer tick_start < tick_end")
            self.tick_start, self.tick_end = ts, te
        else:
            self.tick_start = self.tick_end = -1
        self.frame = 0
        self.tick = -1
        self.sim_seconds = 0.0
        self.shot_progress = 0.0
        self._state: dict[str, dict[str, Any]] = {}
        self._active: dict[str, dict[str, Any]] = {}
        self._spans: list[dict[str, Any]] = []
        self._tracked: dict[str, Boxed] = {}
        self._labels: list[tuple[str, Boxed]] = []  # registered in build(): every frame
        self._frame_labels: list[tuple[str, Boxed]] = []  # registered in frame(): this frame
        self._in_frame = False
        self.set_frame(0)

    # ------------------------------------------------------------------ clock

    def set_frame(self, frame: int) -> None:
        if not 0 <= frame < self.frame_count:
            raise ValueError(f"frame {frame} outside shot of {self.frame_count} frames")
        self.frame = frame
        self.shot_progress = frame / self.frame_count
        self._frame_labels = []
        if self.trace is None:
            return
        self.tick = tick_at(self.tick_start, self.tick_end, frame, self.frame_count)
        self.sim_seconds = float(self.trace.seconds(self.tick))
        self._state = self.trace.state_at(self.tick)
        self._spans = []
        self._active = {}
        for active in self.trace.spans_active(self.tick):
            view = _active_view(active)
            if active.span.entity in self._active:
                raise ValueError(f"{active.span.entity} has more than one active span")
            self._active[active.span.entity] = view
            self._spans.append({**view, "entity": active.span.entity})

    # ------------------------------------------------------------------ trace API

    @property
    def spans(self) -> list[Span]:
        """Every resolved span of the trace (for planning a layout in build())."""
        return [] if self.trace is None else list(self.trace.spans)

    def state(self, entity: str) -> dict[str, Any]:
        """Entity state at the current tick; {} if it is not alive."""
        return dict(self._state.get(entity, {}))

    def alive(self, entity: str) -> bool:
        return entity in self._state

    def active_span(self, entity: str) -> dict[str, Any]:
        """The entity's active span view, or {}. Place entities by `planned_progress`."""
        return dict(self._active.get(entity, {}))

    def last_span(self, entity: str) -> dict[str, Any]:
        """The entity's most recent span with end_tick <= tick: progress 1.0 and
        planned_progress where it stopped (short of 1.0 when cut before its planned end)."""
        if self.trace is None:
            return {}
        span = self.trace.last_span_of(entity, self.tick)
        if span is None:
            return {}
        return span_view(span, 1.0, float(stopped_progress(span)))

    def spans_active(self) -> list[dict[str, Any]]:
        """All active spans, each view plus "entity"."""
        return [dict(s) for s in self._spans]

    def signals_in(self, from_tick: int, to_tick: int) -> list[dict[str, Any]]:
        """`signal` events with from_tick < tick <= to_tick: {tick, entity, name, attrs}."""
        if self.trace is None:
            return []
        return [
            {"tick": e.tick, "entity": e.entity, "name": e.name, "attrs": dict(e.attrs or {})}
            for e in self.trace.events
            if e.type is EventType.SIGNAL and from_tick < e.tick <= to_tick
        ]

    def style(self, key: str, default: Any = None) -> Any:
        tokens = self.job.get("style") or {}
        return tokens.get(key, default) if isinstance(tokens, dict) else default

    # ------------------------------------------------------------------ layout helpers

    def px(self, value: float) -> float:
        """Scene units for `value` pixels on a frame 1080 px high, whatever the output size
        (QA's minimum label height is 28 px at 1080)."""
        return value * self.frame_height / REFERENCE_HEIGHT

    def to_pixels(self, x: float, y: float) -> tuple[float, float]:
        """Scene point -> output pixel (origin top-left)."""
        cx, cy = self.frame_center
        px = (x - (cx - self.frame_width / 2)) * self.width / self.frame_width
        py = ((cy + self.frame_height / 2) - y) * self.height / self.frame_height
        return px, py

    # ------------------------------------------------------------------ annotations

    def track(self, entity: str, mobject: Boxed) -> None:
        """Report `mobject`'s screen bbox for `entity` in every frame's annotation from now
        on. Calling it again for the same entity replaces the mobject (scenes that rebuild
        mobjects in frame() re-track them each frame)."""
        self._tracked[entity] = mobject

    def label(self, text: str, mobject: Boxed) -> None:
        """Report an on-screen label's ink bbox. Called in build(), it is reported on every
        frame it is in the scene; called in frame(), on that frame only."""
        (self._frame_labels if self._in_frame else self._labels).append((text, mobject))

    def screen_box(self, mobject: Boxed, on_screen: set[int]) -> tuple[Box | None, bool]:
        """(bbox clipped to the frame, visible) for a mobject. Visible means: in the scene,
        has points, some part opaque, and the box intersects the frame."""
        family = [m for m in mobject.get_family() if id(m) in on_screen]
        xs: list[float] = []
        ys: list[float] = []
        opaque = False
        for m in family:
            points = getattr(m, "points", None)
            if points is None or len(points) == 0:
                continue
            if _opacity(m) <= 0:
                continue
            opaque = True
            xs += [float(points[:, 0].min()), float(points[:, 0].max())]
            ys += [float(points[:, 1].min()), float(points[:, 1].max())]
        if not opaque:
            return None, False
        x0, y1 = self.to_pixels(min(xs), min(ys))
        x1, y0 = self.to_pixels(max(xs), max(ys))
        cx0, cy0 = max(0.0, x0), max(0.0, y0)
        cx1, cy1 = min(float(self.width), x1), min(float(self.height), y1)
        if cx1 < cx0 or cy1 < cy0 or (cx1 == cx0 and cy1 == cy0):
            return None, False
        return _rounded((cx0, cy0, cx1, cy1)), True

    def label_box(self, mobject: Boxed, on_screen: set[int]) -> Box | None:
        """Unclipped ink bbox of a label in the scene (QA checks bounds and safe areas)."""
        family = [m for m in mobject.get_family() if id(m) in on_screen]
        pts = [
            m.points
            for m in family
            if getattr(m, "points", None) is not None and len(m.points) and _opacity(m) > 0
        ]
        if not pts:
            return None
        xs = [float(p[:, 0].min()) for p in pts] + [float(p[:, 0].max()) for p in pts]
        ys = [float(p[:, 1].min()) for p in pts] + [float(p[:, 1].max()) for p in pts]
        x0, y1 = self.to_pixels(min(xs), min(ys))
        x1, y0 = self.to_pixels(max(xs), max(ys))
        return _rounded((x0, y0, x1, y1))

    def annotation(self, on_screen: set[int]) -> dict[str, Any]:
        """The FrameAnnotation (job.py) of the current frame. `on_screen`: ids of every
        mobject in the scene's family (Scene.get_mobject_family_members())."""
        entities: dict[str, Any] = {}
        for entity, mob in sorted(self._tracked.items()):
            box, visible = self.screen_box(mob, on_screen)
            entities[entity] = {
                "state": dict(self._state.get(entity, {})),
                "active_span": self._active.get(entity),
                "bbox": list(box) if box else None,
                "visible": visible,
            }
        labels = []
        for text, mob in [*self._labels, *self._frame_labels]:
            box = self.label_box(mob, on_screen)
            if box is not None:
                labels.append([text, list(box)])
        return {
            "frame": self.frame,
            "tick": self.tick if self.has_clock else None,
            "entities": entities,
            "labels": labels,
        }

    def run_frame(self, frame: int, draw: Callable[[], None]) -> None:
        """Set the clock to `frame` and call the scene's `frame()` with label() scoped to it."""
        self.set_frame(frame)
        self._in_frame = True
        try:
            draw()
        finally:
            self._in_frame = False


def _opacity(mob: Any) -> float:
    """Largest fill/stroke opacity of one mobject (no family), 1.0 for unknown types."""
    values: list[float] = []
    for getter in ("get_fill_opacity", "get_stroke_opacity"):
        fn = getattr(mob, getter, None)
        if fn is None:
            continue
        try:
            values.append(float(fn()))
        except (TypeError, ValueError, IndexError):
            continue
    if not values:
        return 1.0
    stroke_width = getattr(mob, "stroke_width", None)
    fill = values[0]
    stroke = values[1] if len(values) > 1 and (stroke_width is None or stroke_width > 0) else 0.0
    return max(fill, stroke)


def annotation_line(record: dict[str, Any]) -> str:
    """One annotations.jsonl line: sorted keys, compact, full float precision."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_job(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data
