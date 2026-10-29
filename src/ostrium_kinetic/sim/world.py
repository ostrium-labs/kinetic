"""A minimal deterministic discrete-event loop and the trace recorder bound to it."""

from __future__ import annotations

import heapq
import itertools
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from ostrium_kinetic.canonical import to_fraction
from ostrium_kinetic.sim.model import (
    ENTITY_ID,
    FIELD_TYPES,
    EventType,
    Outcome,
    Scalar,
    Span,
    TraceEvent,
)
from ostrium_kinetic.sim.rng import RngStreams
from ostrium_kinetic.timeline import seconds_to_ticks


class SimulationError(Exception):
    """A simulation broke the trace contract (bad id, unknown field, open span, ...)."""


def _check_scalar(where: str, value: Any) -> None:
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SimulationError(f"{where}: NaN/inf is not allowed in a trace")
        return
    raise SimulationError(f"{where}: {type(value).__name__} is not a trace scalar")


class Recorder:
    """Writes typed trace events. Ticks come from the owning World, so they never go
    backwards, and `seq` gives a total order for events at the same tick."""

    def __init__(self, world: World) -> None:
        self._world = world
        self._seq = itertools.count()
        self.events: list[TraceEvent] = []
        self.entity_types: dict[str, dict[str, str]] = {}
        self._alive: dict[str, str] = {}  # entity id -> type
        self._ever: set[str] = set()
        self._open: dict[str, dict[str, Any]] = {}
        self._spans: list[Span] = []
        self._span_ids: set[str] = set()

    # -- schema ---------------------------------------------------------------------
    def entity_type(self, name: str, **fields: str) -> None:
        """Declare an entity type and its state fields, e.g. entity_type("link", status="str")."""
        if name in self.entity_types:
            raise SimulationError(f"entity type {name!r} declared twice")
        for field, ftype in fields.items():
            if ftype not in FIELD_TYPES:
                raise SimulationError(
                    f"{name}.{field}: type must be one of {sorted(FIELD_TYPES)}, got {ftype!r}"
                )
        self.entity_types[name] = dict(sorted(fields.items()))

    def _check_state(self, entity: str, state: Mapping[str, Scalar], etype: str | None = None) -> None:
        etype = etype or self._alive[entity]
        schema = self.entity_types[etype]
        for field, value in state.items():
            if field not in schema:
                raise SimulationError(f"{entity}: field {field!r} not declared for type {etype!r}")
            _check_scalar(f"{entity}.{field}", value)
            if value is not None and not isinstance(value, FIELD_TYPES[schema[field]]):
                raise SimulationError(f"{entity}.{field}: expected {schema[field]}")
            if schema[field] != "bool" and isinstance(value, bool):
                raise SimulationError(f"{entity}.{field}: expected {schema[field]}, got bool")

    def _emit(self, **fields: Any) -> TraceEvent:
        event = TraceEvent(tick=self._world.now, seq=next(self._seq), **fields)
        self.events.append(event)
        return event

    def _require_alive(self, entity: str) -> None:
        if entity not in self._alive:
            raise SimulationError(f"{entity} does not exist (or was destroyed) at tick {self._world.now}")

    # -- entities -------------------------------------------------------------------
    def create(self, entity: str, **state: Scalar) -> None:
        if not ENTITY_ID.fullmatch(entity):
            raise SimulationError(f"bad entity id {entity!r}; expected '<type>:<name>'")
        if entity in self._ever:
            raise SimulationError(f"entity id {entity!r} reused; ids are never reused in a trace")
        etype = entity.split(":", 1)[0]
        if etype not in self.entity_types:
            raise SimulationError(f"{entity}: entity type {etype!r} not declared")
        self._check_state(entity, state, etype)  # validate fully before registering the id
        self._alive[entity] = etype
        self._ever.add(entity)
        self._emit(
            type=EventType.ENTITY_CREATED, entity=entity, entity_type=etype, state=dict(state)
        )

    def destroy(self, entity: str) -> None:
        self._require_alive(entity)
        for span_id, span in self._open.items():
            if span["entity"] == entity:
                raise SimulationError(f"cannot destroy {entity}: span {span_id} is still open")
        del self._alive[entity]
        self._emit(type=EventType.ENTITY_DESTROYED, entity=entity)

    def set(self, entity: str, **state: Scalar) -> None:
        self._require_alive(entity)
        if not state:
            raise SimulationError(f"{entity}: state.set with no fields")
        self._check_state(entity, state)
        self._emit(type=EventType.STATE_SET, entity=entity, state=dict(state))

    def signal(self, name: str, entity: str, **attrs: Scalar) -> None:
        """An instantaneous, named occurrence: "fault.injected", "timeout.fired", ..."""
        self._require_alive(entity)
        for key, value in attrs.items():
            _check_scalar(f"{name}.{key}", value)
        self._emit(type=EventType.SIGNAL, entity=entity, name=name, attrs=dict(attrs) or None)

    # -- spans ----------------------------------------------------------------------
    def begin(
        self,
        span_id: str,
        entity: str,
        kind: str,
        interp: str = "linear",
        keys: Sequence[tuple[Any, Any]] | None = None,
        attrs: Mapping[str, Scalar] | None = None,
        **kw_attrs: Scalar,
    ) -> None:
        """Open a span. Attributes go in `attrs` (needed for names like "from", "kind" or
        "interp") and/or as keyword arguments; a name given both ways is an error."""
        clash = set(attrs or {}) & set(kw_attrs)
        if clash:
            raise SimulationError(f"span {span_id!r}: attribute(s) given twice: {sorted(clash)}")
        attrs = {**(attrs or {}), **kw_attrs}
        self._require_alive(entity)
        if span_id in self._span_ids:
            raise SimulationError(f"span id {span_id!r} reused")
        norm_keys = _check_interp(span_id, interp, keys)
        for key, value in attrs.items():
            _check_scalar(f"{span_id}.{key}", value)
        self._span_ids.add(span_id)
        self._open[span_id] = {
            "entity": entity,
            "kind": kind,
            "interp": interp,
            "keys": norm_keys,
            "attrs": dict(attrs),
            "begin_tick": self._world.now,
        }
        self._emit(
            type=EventType.SPAN_BEGIN,
            entity=entity,
            span_id=span_id,
            kind=kind,
            interp=interp,
            keys=norm_keys,
            attrs=dict(attrs) or None,
        )

    def end(self, span_id: str, outcome: Outcome | str = Outcome.COMPLETED) -> None:
        if span_id not in self._open:
            raise SimulationError(f"span {span_id!r} is not open")
        span = self._open[span_id]
        try:
            outcome = Outcome(outcome)
        except ValueError as exc:
            raise SimulationError(f"span {span_id!r}: unknown outcome {outcome!r}") from exc
        if self._world.now == span["begin_tick"] and span["interp"] != "step":
            raise SimulationError(f"span {span_id!r} has zero duration; use interp='step'")
        del self._open[span_id]  # only once the end is known to be valid
        self._spans.append(
            Span(
                span_id=span_id,
                entity=span["entity"],
                kind=span["kind"],
                begin_tick=span["begin_tick"],
                end_tick=self._world.now,
                outcome=outcome,
                interp=span["interp"],
                keys=span["keys"],
                attrs=span["attrs"],
            )
        )
        self._emit(type=EventType.SPAN_END, entity=span["entity"], span_id=span_id, outcome=outcome)

    def is_open(self, span_id: str) -> bool:
        return span_id in self._open

    def finish(self) -> list[Span]:
        if self._open:
            raise SimulationError(
                "spans still open at end of simulation: " + ", ".join(sorted(self._open))
            )
        # Spans are resolved in end order; publish them in begin order (ties by id).
        return sorted(self._spans, key=lambda s: (s.begin_tick, s.end_tick, s.span_id))


def _check_interp(
    span_id: str, interp: str, keys: Sequence[tuple[Any, Any]] | None
) -> list[tuple[Fraction, Fraction]] | None:
    if interp in ("step", "linear"):
        if keys is not None:
            raise SimulationError(f"span {span_id!r}: keys are only allowed with interp 'curve:<name>'")
        return None
    if not interp.startswith("curve:") or len(interp) == len("curve:"):
        raise SimulationError(f"span {span_id!r}: interp must be step, linear or curve:<name>")
    if not keys or len(keys) < 2:
        raise SimulationError(f"span {span_id!r}: a curve needs at least two keys")
    norm = [(to_fraction(u), to_fraction(p)) for u, p in keys]
    if norm[0][0] != 0 or norm[-1][0] != 1:
        raise SimulationError(f"span {span_id!r}: curve keys must start at u=0 and end at u=1")
    if any(b[0] <= a[0] for a, b in itertools.pairwise(norm)):
        raise SimulationError(f"span {span_id!r}: curve key times must strictly increase")
    if any(not 0 <= p <= 1 for _, p in norm):
        raise SimulationError(f"span {span_id!r}: curve progress must be within [0, 1]")
    return norm


@dataclass
class _Scheduled:
    fn: Callable[..., None]
    args: tuple[Any, ...]


class World:
    """Discrete-event loop over integer ticks. Callbacks scheduled for the same tick run
    in the order they were scheduled."""

    def __init__(self, tick_rate: Fraction) -> None:
        if tick_rate <= 0:
            raise ValueError("tick_rate must be > 0 seconds per tick")
        self.tick_rate = tick_rate
        self.now = 0
        self._queue: list[tuple[int, int, _Scheduled]] = []
        self._order = itertools.count()
        self.rec = Recorder(self)

    def ticks(self, seconds: Any) -> int:
        """Exact seconds (int, decimal string, Fraction) to ticks; must divide evenly."""
        return seconds_to_ticks(to_fraction(seconds), self.tick_rate)

    def at(self, tick: int, fn: Callable[..., None], *args: Any) -> None:
        if tick < self.now:
            raise SimulationError(f"cannot schedule at tick {tick}; now is {self.now}")
        heapq.heappush(self._queue, (tick, next(self._order), _Scheduled(fn, args)))

    def after(self, delay_ticks: int, fn: Callable[..., None], *args: Any) -> None:
        if delay_ticks < 0:
            raise SimulationError("delay must be >= 0 ticks")
        self.at(self.now + delay_ticks, fn, *args)

    def run(self, until: int | None = None) -> None:
        while self._queue:
            tick, _, item = self._queue[0]
            if until is not None and tick > until:
                break
            heapq.heappop(self._queue)
            self.now = tick
            item.fn(*item.args)
        if until is not None:
            self.now = max(self.now, until)


@dataclass
class SimContext:
    """What a simulation's `run(ctx)` receives."""

    params: Mapping[str, Any]
    rng: RngStreams
    world: World

    @property
    def rec(self) -> Recorder:
        return self.world.rec
