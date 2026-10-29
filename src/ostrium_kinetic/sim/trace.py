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


@dataclass(frozen=True)
class ActiveSpan:
    span: Span
    progress: Fraction  # 0 <= progress < 1 while active


def span_progress(span: Span, tick: int) -> Fraction:
    """Progress of `span` at `tick`, by its interpolation mode. Caller ensures it is active."""
    if span.interp == "step":
        return Fraction(0)
    u = Fraction(tick - span.begin_tick, span.end_tick - span.begin_tick)
    if span.interp == "linear":
        return u
    keys = span.keys or []
    for (u0, p0), (u1, p1) in itertools.pairwise(keys):
        if u0 <= u <= u1:
            return p0 + (p1 - p0) * (u - u0) / (u1 - u0)
    raise ValueError(f"span {span.span_id}: u={u} outside curve keys")


class Trace:
    def __init__(self, events: list[TraceEvent], spans: list[Span], meta: TraceMeta) -> None:
        self.events = events
        self.spans = spans
        self.meta = meta
        self._ticks = [e.tick for e in events]

    @classmethod
    def load(cls, directory: Path) -> Trace:
        meta = TraceMeta.model_validate_json((directory / META_FILE).read_text("utf-8"))
        events = [TraceEvent.model_validate_json(line) for line in _lines(directory / TRACE_FILE)]
        spans = [Span.model_validate_json(line) for line in _lines(directory / SPANS_FILE)]
        for name, expected in meta.files.items():
            actual = canonical.sha256_bytes((directory / name).read_bytes())
            if actual != expected:
                raise ValueError(f"{directory / name}: sha256 mismatch (trace was modified)")
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
            ActiveSpan(span, span_progress(span, tick))
            for span in self.spans
            if span.begin_tick <= tick < span.end_tick
        ]

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
    return "".join(canonical.dumps(r.model_dump(mode="json", exclude_none=True)) + "\n" for r in records).encode("utf-8")


def read_meta_dict(directory: Path) -> dict[str, Any]:
    return json.loads((directory / META_FILE).read_text("utf-8"))
