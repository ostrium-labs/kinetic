"""Checkpoint expectation semantics, independent of where the observed values come from.

`contract/validate.py` evaluates expectations against a Trace; QA evaluates the same
expectations against what a renderer annotated. Both reduce an entity at a tick to
(state dict or None, active-span dict or None) and compare with these functions, so the
semantics cannot drift apart.

Span dicts use the runtime's shape: span_id, kind, interp, outcome, begin_tick, end_tick,
planned_end_tick when the span has one, the span's attrs, and optionally progress and
planned_progress. Fixed span fields win over attrs of the same name.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ostrium_kinetic.contract.models import Expectation
from ostrium_kinetic.sim.trace import Trace

MISSING: Any = object()  # the value is absent (entity not alive, no span, no such field)

SPAN_FIELDS: tuple[str, ...] = ("span_id", "kind", "interp", "outcome", "begin_tick", "end_tick")


def span_view(span: Any) -> dict[str, Any]:
    """A trace `Span` as the dict a runtime exposes from `active_span(entity)`."""
    view: dict[str, Any] = dict(span.attrs)
    view.update(
        span_id=span.span_id,
        kind=span.kind,
        interp=span.interp,
        outcome=span.outcome.value,
        begin_tick=span.begin_tick,
        end_tick=span.end_tick,
    )
    if span.planned_end_tick is not None:
        view["planned_end_tick"] = span.planned_end_tick
    return view


def observed(
    exp: Expectation,
    state: Mapping[str, Any] | None,
    active_span: Mapping[str, Any] | None,
) -> Any:
    """The value `exp` looks at. `state` is None when the entity is not alive, `active_span`
    None when it has no active span. `active_span` alone yields the span id (or None)."""
    if exp.field == "active_span":
        return None if active_span is None else active_span.get("span_id", MISSING)
    if exp.field.startswith("active_span."):
        if active_span is None:
            return MISSING
        return active_span.get(exp.field.removeprefix("active_span."), MISSING)
    if state is None:
        return MISSING
    return state.get(exp.field, MISSING)


def trace_observed(trace: Trace, exp: Expectation, tick: int) -> Any:
    """`observed` against the trace: the reference value for `exp` at `tick`.
    Raises ValueError when the entity has more than one active span."""
    active = trace.active_span_of(exp.entity, tick)
    span = None if active is None else span_view(active.span)
    return observed(exp, trace.state_at(tick).get(exp.entity), span)


def matches(actual: Any, expected: Any) -> bool:
    """Equality with booleans kept distinct from numbers (True != 1); absent never matches."""
    if actual is MISSING:
        return False
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def show(value: Any) -> str:
    return "<absent>" if value is MISSING else repr(value)
