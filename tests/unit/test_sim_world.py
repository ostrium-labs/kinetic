from __future__ import annotations

from fractions import Fraction
from typing import Any

import pytest

from ostrium_kinetic.sim import EventType, Outcome, Recorder, SimulationError, World

MS = Fraction(1, 1000)


@pytest.fixture
def world() -> World:
    w = World(MS)
    w.rec.entity_type("node", role="str", load="float", up="bool", count="int")
    w.rec.entity_type("packet", status="str")
    return w


@pytest.fixture
def rec(world: World) -> Recorder:
    return world.rec


# -- the event loop ---------------------------------------------------------------------


def test_world_rejects_non_positive_tick_rate() -> None:
    with pytest.raises(ValueError):
        World(Fraction(0))
    with pytest.raises(ValueError):
        World(Fraction(-1, 1000))


def test_ticks_converts_exact_seconds(world: World) -> None:
    assert world.ticks("1.2") == 1200
    assert world.ticks(2) == 2000
    assert world.ticks(Fraction(1, 4)) == 250
    with pytest.raises(ValueError):
        world.ticks("0.0005")


def test_callbacks_run_in_tick_order_and_fifo_within_a_tick(world: World) -> None:
    log: list[tuple[int, str]] = []

    def note(label: str) -> None:
        log.append((world.now, label))

    world.at(5, note, "a")
    world.at(3, note, "b")
    world.at(5, note, "c")
    world.at(0, note, "d")
    world.at(5, note, "e")
    world.run()
    assert log == [(0, "d"), (3, "b"), (5, "a"), (5, "c"), (5, "e")]


def test_same_tick_scheduling_from_a_callback_runs_after_queued_work(world: World) -> None:
    log: list[str] = []

    def first() -> None:
        log.append("first")
        world.after(0, log.append, "scheduled-by-first")

    world.at(10, first)
    world.at(10, log.append, "second")
    world.run()
    assert log == ["first", "second", "scheduled-by-first"]


def test_cannot_schedule_in_the_past(world: World) -> None:
    errors: list[Exception] = []

    def late() -> None:
        try:
            world.at(world.now - 1, lambda: None)
        except SimulationError as exc:
            errors.append(exc)

    world.at(10, late)
    world.run()
    assert len(errors) == 1
    with pytest.raises(SimulationError):
        world.after(-1, lambda: None)


def test_run_until_stops_and_advances_clock(world: World) -> None:
    log: list[int] = []
    world.at(100, log.append, 100)
    world.at(500, log.append, 500)
    world.at(501, log.append, 501)
    world.run(until=500)
    assert log == [100, 500]  # the until tick is inclusive
    assert world.now == 500

    idle = World(MS)
    idle.run(until=3500)
    assert idle.now == 3500


# -- entity types and ids ---------------------------------------------------------------


def test_entity_type_declared_twice(rec: Recorder) -> None:
    with pytest.raises(SimulationError, match="declared twice"):
        rec.entity_type("node", role="str")


def test_entity_type_rejects_unknown_field_type(world: World) -> None:
    with pytest.raises(SimulationError, match="type must be one of"):
        world.rec.entity_type("thing", items="list")


def test_entity_type_fields_are_sorted(rec: Recorder) -> None:
    assert list(rec.entity_types["node"]) == ["count", "load", "role", "up"]


@pytest.mark.parametrize(
    "entity_id",
    ["node", "node:", ":A", "Node:A", "1node:A", "node:A B", "node:A/B", "node:Ä", ""],
)
def test_bad_entity_ids_rejected(rec: Recorder, entity_id: str) -> None:
    with pytest.raises(SimulationError, match="bad entity id"):
        rec.create(entity_id)


@pytest.mark.parametrize("entity_id", ["node:A", "node:a-1", "node:n.2", "node:X_9"])
def test_good_entity_ids_accepted(rec: Recorder, entity_id: str) -> None:
    rec.create(entity_id, role="r")
    assert rec.events[-1].entity == entity_id


def test_undeclared_entity_type(rec: Recorder) -> None:
    with pytest.raises(SimulationError, match="not declared"):
        rec.create("ghost:1")


def test_undeclared_field(rec: Recorder) -> None:
    with pytest.raises(SimulationError, match="not declared for type"):
        rec.create("node:A", colour="red")
    rec.create("node:B", role="r")
    with pytest.raises(SimulationError, match="not declared for type"):
        rec.set("node:B", colour="red")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("count", True),  # bool is not an int here
        ("count", False),
        ("count", "3"),
        ("count", 1.5),
        ("load", True),  # nor a float
        ("load", "0.5"),
        ("role", 1),
        ("role", True),
        ("up", 1),
        ("up", "true"),
    ],
)
def test_field_type_checks(rec: Recorder, field: str, value: object) -> None:
    with pytest.raises(SimulationError):
        rec.create("node:A", **{field: value})  # type: ignore[arg-type]


def test_field_types_accepted(rec: Recorder) -> None:
    rec.create("node:A", count=3, load=0.5, role="r", up=True)
    rec.set("node:A", load=2)  # an int is a valid float
    rec.set("node:A", count=None, role=None)  # None clears any field
    assert rec.events[-2].state == {"load": 2}


@pytest.mark.parametrize("value", [[1], {"a": 1}, Fraction(1, 2), object()])
def test_non_scalar_rejected(rec: Recorder, value: object) -> None:
    rec.create("node:A", role="r")
    with pytest.raises(SimulationError, match="not a trace scalar"):
        rec.signal("odd", "node:A", payload=value)  # type: ignore[arg-type]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nan_and_inf_rejected(rec: Recorder, value: float) -> None:
    rec.create("node:A", role="r")
    with pytest.raises(SimulationError, match="NaN/inf"):
        rec.set("node:A", load=value)
    with pytest.raises(SimulationError, match="NaN/inf"):
        rec.signal("measure", "node:A", value=value)
    with pytest.raises(SimulationError, match="NaN/inf"):
        rec.begin("s:1", "node:A", "work", value=value)
    with pytest.raises(SimulationError, match="NaN/inf"):
        rec.create("node:B", load=value)


def test_entity_ids_are_never_reused(world: World, rec: Recorder) -> None:
    rec.create("node:A", role="r")
    with pytest.raises(SimulationError, match="reused"):
        rec.create("node:A", role="r")
    world.now = 10
    rec.destroy("node:A")
    with pytest.raises(SimulationError, match="reused"):
        rec.create("node:A", role="r")


def test_operations_on_missing_or_destroyed_entities(rec: Recorder) -> None:
    with pytest.raises(SimulationError, match="does not exist"):
        rec.set("node:A", role="x")
    with pytest.raises(SimulationError, match="does not exist"):
        rec.signal("ping", "node:A")
    with pytest.raises(SimulationError, match="does not exist"):
        rec.destroy("node:A")
    rec.create("node:A", role="r")
    rec.destroy("node:A")
    with pytest.raises(SimulationError, match="does not exist"):
        rec.set("node:A", role="x")
    with pytest.raises(SimulationError, match="does not exist"):
        rec.begin("s:1", "node:A", "work")


def test_set_requires_fields(rec: Recorder) -> None:
    rec.create("node:A", role="r")
    with pytest.raises(SimulationError, match="no fields"):
        rec.set("node:A")


def test_destroy_with_open_span(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="in_flight")
    rec.begin("tx:1", "packet:1", "transit")
    world.now = 5
    with pytest.raises(SimulationError, match="still open"):
        rec.destroy("packet:1")
    rec.end("tx:1")
    rec.destroy("packet:1")
    assert rec.events[-1].type is EventType.ENTITY_DESTROYED


# -- events -----------------------------------------------------------------------------


def test_events_carry_tick_and_monotonic_seq(world: World, rec: Recorder) -> None:
    rec.create("node:A", role="r")
    world.now = 7
    rec.set("node:A", role="s")
    rec.signal("fault.injected", "node:A", fault="cut")
    assert [(e.tick, e.seq) for e in rec.events] == [(0, 0), (7, 1), (7, 2)]
    created, setter, signal = rec.events
    assert created.type is EventType.ENTITY_CREATED
    assert created.entity_type == "node"
    assert created.state == {"role": "r"}
    assert setter.type is EventType.STATE_SET
    assert signal.type is EventType.SIGNAL
    assert signal.name == "fault.injected"
    assert signal.attrs == {"fault": "cut"}


def test_signal_without_attrs_has_no_attrs(rec: Recorder) -> None:
    rec.create("node:A", role="r")
    rec.signal("ping", "node:A")
    assert rec.events[-1].attrs is None


# -- spans ------------------------------------------------------------------------------


def test_span_lifecycle(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="in_flight")
    ends: dict[str, Any] = {"from": "node:A", "to": "node:B"}  # "from" is a keyword
    rec.begin("tx:1", "packet:1", "transit", **ends)
    assert rec.is_open("tx:1")
    begin = rec.events[-1]
    assert begin.type is EventType.SPAN_BEGIN
    assert (begin.span_id, begin.kind, begin.interp) == ("tx:1", "transit", "linear")

    world.now = 1200
    rec.end("tx:1", "lost")
    assert not rec.is_open("tx:1")
    end = rec.events[-1]
    assert end.type is EventType.SPAN_END
    assert end.outcome is Outcome.LOST
    assert end.entity == "packet:1"

    (span,) = rec.finish()
    assert span.span_id == "tx:1"
    assert (span.begin_tick, span.end_tick) == (0, 1200)
    assert span.outcome is Outcome.LOST
    assert span.interp == "linear"
    assert span.keys is None
    assert span.attrs == {"from": "node:A", "to": "node:B"}


def test_span_default_outcome_is_completed(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("tx:1", "packet:1", "transit")
    world.now = 3
    rec.end("tx:1")
    assert rec.finish()[0].outcome is Outcome.COMPLETED


def test_span_id_reuse_and_unknown_end(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("tx:1", "packet:1", "transit")
    with pytest.raises(SimulationError, match="reused"):
        rec.begin("tx:1", "packet:1", "transit")
    world.now = 1
    rec.end("tx:1")
    with pytest.raises(SimulationError, match="reused"):
        rec.begin("tx:1", "packet:1", "transit")
    with pytest.raises(SimulationError, match="not open"):
        rec.end("tx:1")
    with pytest.raises(SimulationError, match="not open"):
        rec.end("tx:never")


def test_zero_duration_linear_span_rejected(rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("tx:1", "packet:1", "transit")
    with pytest.raises(SimulationError, match="zero duration"):
        rec.end("tx:1")


def test_zero_duration_curve_span_rejected(rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("c:1", "packet:1", "ramp", interp="curve:ease", keys=[(0, 0), (1, 1)])
    with pytest.raises(SimulationError, match="zero duration"):
        rec.end("c:1")


def test_zero_duration_step_span_allowed(rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("mark:1", "packet:1", "marker", interp="step")
    rec.end("mark:1")
    (span,) = rec.finish()
    assert span.begin_tick == span.end_tick == 0


def test_curve_keys_normalised_to_fractions(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin(
        "c:1", "packet:1", "ramp", interp="curve:slow_start", keys=[(0, 0), ("0.5", "1/4"), (1, 1)]
    )
    assert rec.events[-1].keys == [
        (Fraction(0), Fraction(0)),
        (Fraction(1, 2), Fraction(1, 4)),
        (Fraction(1), Fraction(1)),
    ]
    world.now = 10
    rec.end("c:1")
    assert rec.finish()[0].keys == rec.events[-2].keys


@pytest.mark.parametrize(
    ("interp", "keys", "message"),
    [
        ("linear", [(0, 0), (1, 1)], "only allowed"),
        ("step", [(0, 0), (1, 1)], "only allowed"),
        ("ease", None, "step, linear or curve"),
        ("curve:", [(0, 0), (1, 1)], "step, linear or curve"),
        ("curve:x", None, "at least two keys"),
        ("curve:x", [(0, 0)], "at least two keys"),
        ("curve:x", [("0.1", 0), (1, 1)], "start at u=0"),
        ("curve:x", [(0, 0), ("0.9", 1)], "end at u=1"),
        ("curve:x", [(0, 0), ("0.5", "0.5"), ("0.5", "0.6"), (1, 1)], "strictly increase"),
        ("curve:x", [(0, 0), ("0.6", "0.5"), ("0.4", "0.6"), (1, 1)], "strictly increase"),
        ("curve:x", [(0, 0), ("0.5", "1.5"), (1, 1)], "within"),
        ("curve:x", [(0, "-0.1"), (1, 1)], "within"),
    ],
)
def test_curve_key_validation(
    rec: Recorder, interp: str, keys: list[tuple[object, object]] | None, message: str
) -> None:
    rec.create("packet:1", status="x")
    with pytest.raises(SimulationError, match=message):
        rec.begin("c:1", "packet:1", "ramp", interp=interp, keys=keys)
    assert not rec.is_open("c:1")


def test_finish_with_open_span(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("tx:b", "packet:1", "transit")
    rec.begin("tx:a", "packet:1", "transit")
    with pytest.raises(SimulationError, match=r"still open.*tx:a, tx:b"):
        rec.finish()


def test_finish_publishes_spans_in_begin_order(world: World, rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.create("packet:2", status="x")
    rec.begin("tx:long", "packet:1", "transit")
    world.now = 5
    rec.begin("tx:short", "packet:2", "transit")
    world.now = 6
    rec.end("tx:short")  # ends first
    world.now = 9
    rec.end("tx:long")
    assert [s.span_id for s in rec.finish()] == ["tx:long", "tx:short"]


# -- known defects in src (strict xfail: these start passing once fixed) ----------------


def test_rejected_create_does_not_burn_the_id(rec: Recorder) -> None:
    with pytest.raises(SimulationError):
        rec.create("node:A", colour="red")
    rec.create("node:A", role="r")


@pytest.mark.xfail(
    strict=True,
    reason="Recorder.end pops the open span before the zero-duration check, so after the "
    "error the span is neither open nor resolved and finish() does not notice",
)
def test_rejected_end_keeps_span_open(rec: Recorder) -> None:
    rec.create("packet:1", status="x")
    rec.begin("tx:1", "packet:1", "transit")
    with pytest.raises(SimulationError):
        rec.end("tx:1")
    assert rec.is_open("tx:1")


def test_entity_id_with_trailing_newline_rejected_as_simulation_error(rec: Recorder) -> None:
    with pytest.raises(SimulationError, match="bad entity id"):
        rec.create("node:A\n", role="r")
