"""Trace contract v0.1: the typed records a simulation produces and every renderer reads.

The simulation decides what happened and when. Renderers decide geometry and look.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ostrium_kinetic.canonical import Exact

ENTITY_ID = re.compile(r"^[a-z][a-z0-9_-]*:[A-Za-z0-9_.-]+$")
EntityId = Annotated[str, Field(pattern=ENTITY_ID.pattern)]

# Values allowed in entity state and span/signal attributes. Floats are allowed for
# quantities, but never for time: time is always integer ticks.
Scalar = str | int | float | bool | None

FIELD_TYPES: dict[str, type | tuple[type, ...]] = {
    "str": str,
    "int": int,
    "float": (int, float),
    "bool": bool,
}


class EventType(StrEnum):
    ENTITY_CREATED = "entity.created"
    ENTITY_DESTROYED = "entity.destroyed"
    STATE_SET = "state.set"
    SIGNAL = "signal"
    SPAN_BEGIN = "span.begin"
    SPAN_END = "span.end"


class Outcome(StrEnum):
    COMPLETED = "completed"
    LOST = "lost"
    ABORTED = "aborted"


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TraceEvent(_Record):
    """One line of trace.jsonl. Total order is (tick, seq)."""

    tick: Annotated[int, Field(ge=0)]
    seq: Annotated[int, Field(ge=0)]
    type: EventType
    entity: EntityId
    entity_type: str | None = None  # entity.created
    state: dict[str, Scalar] | None = None  # entity.created, state.set
    name: str | None = None  # signal, e.g. "fault.injected", "timeout.fired"
    span_id: str | None = None  # span.begin / span.end
    kind: str | None = None  # span.begin, e.g. "transit"
    interp: str | None = None  # span.begin
    keys: list[tuple[Exact, Exact]] | None = None  # span.begin with interp "curve:<name>"
    outcome: Outcome | None = None  # span.end
    attrs: dict[str, Scalar] | None = None


class Span(_Record):
    """One line of spans.jsonl: a resolved interval, so renderers never need lookahead.

    interp:
      "step"         no continuous progress; the span is simply active
      "linear"       progress = (t - begin) / (end - begin)
      "curve:<name>" piecewise-linear over `keys` [(u, progress)], u = normalised time
    """

    span_id: str
    entity: EntityId
    kind: str
    begin_tick: Annotated[int, Field(ge=0)]
    end_tick: Annotated[int, Field(ge=0)]
    outcome: Outcome
    interp: str
    keys: list[tuple[Exact, Exact]] | None = None
    attrs: dict[str, Scalar] = Field(default_factory=dict)


class SimSource(_Record):
    module: str
    source_sha256: str


class TraceMeta(_Record):
    contract: Literal["0.1"] = "0.1"
    kinetic_version: str
    tick_rate: Exact  # seconds per tick
    end_tick: int
    seed: int
    params: dict[str, Any]
    sim: SimSource
    entity_types: dict[str, dict[str, str]]
    event_count: int
    span_count: int
    files: dict[str, str]  # file name -> sha256
