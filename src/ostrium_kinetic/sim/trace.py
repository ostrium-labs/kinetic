"""Reading a trace back and sampling it at a tick. This is the reference implementation that
renderer-side runtimes (GDScript, bpy, Manim) must match."""

from __future__ import annotations

import bisect
import itertools
import json
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from ostrium_kinetic import canonical
from ostrium_kinetic.sim.model import EventType, Span, TraceEvent, TraceMeta

TRACE_FILE = "trace.jsonl"
SPANS_FILE = "spans.jsonl"
META_FILE = "trace.meta.json"
META_SHA_FILE = "trace.meta.sha256"  # sidecar: the meta cannot contain its own hash


@dataclass(frozen=True)
class ActiveSpan:
    span: Span
    progress: Fraction  # 0 <= progress < 1 while active
    planned_progress: Fraction  # progress over the planned interval; == progress without one


def _interp_at(span: Span, u: Fraction) -> Fraction:
    if span.interp == "step":
        return Fraction(0)
    if span.interp == "linear":
        return u
    keys = span.keys or []
    for (u0, p0), (u1, p1) in itertools.pairwise(keys):
        if u0 <= u <= u1:
            return p0 + (p1 - p0) * (u - u0) / (u1 - u0)
    raise ValueError(f"span {span.span_id}: u={u} outside curve keys")


def span_progress(span: Span, tick: int) -> Fraction:
    """Progress of `span` at `tick` through its actual interval [begin_tick, end_tick], by its
    interpolation mode. Caller ensures it is active. A span cut short reaches 1 at the cut;
    use `planned_progress` to place an entity on an interrupted path."""
    if span.interp == "step":
        return Fraction(0)
    return _interp_at(span, Fraction(tick - span.begin_tick, span.end_tick - span.begin_tick))


def planned_progress(span: Span, tick: int) -> Fraction:
    """Progress of `span` at `tick` over its planned interval [begin_tick, planned_end_tick],
    with the span's interp applied there. Equal to `span_progress` when the span has no
    planned end. Defined for begin_tick <= tick <= end_tick (end_tick <= planned_end_tick)."""
    if span.planned_end_tick is None or span.interp == "step":
        return span_progress(span, tick)
    return _interp_at(
        span, Fraction(tick - span.begin_tick, span.planned_end_tick - span.begin_tick)
    )


def stopped_progress(span: Span) -> Fraction:
    """Where an ended span stopped: its planned progress at end_tick. For a span cut short
    this is the position of the cut (e.g. 1200/1488 for the lost failover packet); for a span
    that ran to its planned end (or has none) it is the progress at its end (1 for linear)."""
    return planned_progress(span, span.end_tick)


class Trace:
    def __init__(self, events: list[TraceEvent], spans: list[Span], meta: TraceMeta) -> None:
        self.events = events
        self.spans = spans
        self.meta = meta
        self._ticks = [e.tick for e in events]

    @classmethod
    def load(cls, directory: Path) -> Trace:
        meta_bytes = (directory / META_FILE).read_bytes()
        sidecar = directory / META_SHA_FILE
        if not sidecar.is_file():
            raise ValueError(f"{sidecar}: missing (cannot verify {META_FILE})")
        if sidecar.read_text("utf-8").strip() != canonical.sha256_bytes(meta_bytes):
            raise ValueError(f"{directory / META_FILE}: sha256 mismatch (trace meta was modified)")
        meta = TraceMeta.model_validate_json(meta_bytes)
        if set(meta.files) != {TRACE_FILE, SPANS_FILE}:
            raise ValueError(
                f"{directory / META_FILE}: `files` must list exactly {TRACE_FILE} and "
                f"{SPANS_FILE}, got {sorted(meta.files)}"
            )
        events = [TraceEvent.model_validate_json(line) for line in _lines(directory / TRACE_FILE)]
        spans = [Span.model_validate_json(line) for line in _lines(directory / SPANS_FILE)]
        for name, expected in meta.files.items():
            actual = canonical.sha256_bytes((directory / name).read_bytes())
            if actual != expected:
                raise ValueError(f"{directory / name}: sha256 mismatch (trace was modified)")
        if len(events) != meta.event_count or len(spans) != meta.span_count:
            raise ValueError(f"{directory}: event/span counts disagree with {META_FILE}")
        if events and events[-1].tick > meta.end_tick:
            raise ValueError(f"{directory}: events run past end_tick in {META_FILE}")
        return cls(events, spans, meta)

    def seconds(self, tick: int) -> Fraction:
        return tick * self.meta.tick_rate

    def state_at(self, tick: int) -> dict[str, dict[str, Any]]:
        """State of every entity alive at `tick`, after all events with event.tick <= tick."""
        end = bisect.bisect_right(self._ticks, tick)
        states: dict[str, dict[str, Any]] = {}
        for event in self.events[:end]:
            if event.type is EventType.ENTITY_CREATED:
                states[event.entity] = dict(event.state or {})
            elif event.type is EventType.STATE_SET:
                states[event.entity].update(event.state or {})
            elif event.type is EventType.ENTITY_DESTROYED:
                del states[event.entity]
        return states

    def spans_active(self, tick: int) -> list[ActiveSpan]:
        """Spans with begin_tick <= tick < end_tick. A span is no longer active on its end tick."""
        return [
            ActiveSpan(span, span_progress(span, tick), planned_progress(span, tick))
            for span in self.spans
            if span.begin_tick <= tick < span.end_tick
        ]

    def last_span_of(self, entity: str, tick: int) -> Span | None:
        """The entity's most recent span that has ended by `tick` (end_tick <= tick); ties go
        to the later one in spans.jsonl order. Shows where an entity ended up; place it with
        `stopped_progress(span)`, which is short of 1 for a span cut before its planned end."""
        best: Span | None = None
        for span in self.spans:
            if (
                span.entity == entity
                and span.end_tick <= tick
                and (best is None or span.end_tick >= best.end_tick)
            ):
                best = span
        return best

    def active_span_of(self, entity: str, tick: int) -> ActiveSpan | None:
        found = [a for a in self.spans_active(tick) if a.span.entity == entity]
        if len(found) > 1:
            raise ValueError(f"{entity} has {len(found)} active spans at tick {tick}")
        return found[0] if found else None


def _lines(path: Path) -> Iterator[str]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield line


def encode_jsonl(records: list[Any]) -> bytes:
    return "".join(
        canonical.dumps(r.model_dump(mode="json", exclude_none=True)) + "\n" for r in records
    ).encode("utf-8")


def read_meta_dict(directory: Path) -> dict[str, Any]:
    return json.loads((directory / META_FILE).read_text("utf-8"))
