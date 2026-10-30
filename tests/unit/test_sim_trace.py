from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from ostrium_kinetic.sim import Outcome, Span, Trace, TraceMeta, World
from ostrium_kinetic.sim.model import SimSource
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.sim.trace import (
    SPANS_FILE,
    TRACE_FILE,
    planned_progress,
    span_progress,
    stopped_progress,
)

MS = Fraction(1, 1000)


def _trace_from(world: World) -> Trace:
    spans = world.rec.finish()
    meta = TraceMeta(
        kinetic_version="test",
        tick_rate=world.tick_rate,
        end_tick=world.now,
        seed=0,
        params={},
        sim=SimSource(module="inline", source_sha256="0" * 64),
        entity_types=world.rec.entity_types,
        event_count=len(world.rec.events),
        span_count=len(spans),
        files={},
    )
    return Trace(world.rec.events, spans, meta)


@pytest.fixture
def trace() -> Trace:
    """node:A created at 0; set at 10 (twice, same tick); destroyed at 20.
    packet:1 transit 100..200 (linear), packet:1 hold 300..400 (step),
    packet:2 ramp 100..200 (curve keys (0,0) (1/2,1/4) (1,1))."""
    w = World(MS)
    rec = w.rec
    rec.entity_type("node", status="str", load="int")
    rec.entity_type("packet", status="str")

    rec.create("node:A", status="up", load=0)
    rec.create("packet:1", status="queued")
    rec.create("packet:2", status="queued")
    w.now = 10
    rec.set("node:A", load=5)
    rec.set("node:A", status="busy", load=7)
    w.now = 20
    rec.destroy("node:A")
    w.now = 100
    rec.begin("tx:1", "packet:1", "transit", via="link:P")
    rec.set("packet:1", status="in_flight")
    rec.begin(
        "ramp:2", "packet:2", "ramp", interp="curve:ease", keys=[(0, 0), ("1/2", "1/4"), (1, 1)]
    )
    w.now = 200
    rec.end("tx:1", Outcome.COMPLETED)
    rec.set("packet:1", status="delivered")
    rec.end("ramp:2")
    w.now = 300
    rec.begin("hold:1", "packet:1", "hold", interp="step")
    w.now = 400
    rec.end("hold:1", Outcome.ABORTED)
    return _trace_from(w)


# -- state_at ---------------------------------------------------------------------------


def test_state_at_creation_tick_is_inclusive(trace: Trace) -> None:
    assert trace.state_at(0)["node:A"] == {"status": "up", "load": 0}


def test_state_at_applies_every_event_at_the_tick(trace: Trace) -> None:
    assert trace.state_at(9)["node:A"] == {"status": "up", "load": 0}
    assert trace.state_at(10)["node:A"] == {"status": "busy", "load": 7}
    assert trace.state_at(19)["node:A"] == {"status": "busy", "load": 7}


def test_state_at_destroyed_entity_is_gone_on_its_tick(trace: Trace) -> None:
    assert "node:A" in trace.state_at(19)
    assert "node:A" not in trace.state_at(20)
    assert "node:A" not in trace.state_at(10_000)


def test_state_at_step_semantics_for_set(trace: Trace) -> None:
    assert trace.state_at(99)["packet:1"]["status"] == "queued"
    assert trace.state_at(100)["packet:1"]["status"] == "in_flight"
    assert trace.state_at(199)["packet:1"]["status"] == "in_flight"
    assert trace.state_at(200)["packet:1"]["status"] == "delivered"


def test_state_at_returns_independent_copies(trace: Trace) -> None:
    first = trace.state_at(0)
    first["packet:1"]["status"] = "mutated"
    assert trace.state_at(0)["packet:1"]["status"] == "queued"
    assert trace.events[1].state == {"status": "queued"}


def test_state_at_far_future(trace: Trace) -> None:
    assert set(trace.state_at(10**9)) == {"packet:1", "packet:2"}


# -- spans ------------------------------------------------------------------------------


def test_spans_active_excludes_end_tick(trace: Trace) -> None:
    assert [a.span.span_id for a in trace.spans_active(99)] == []
    assert sorted(a.span.span_id for a in trace.spans_active(100)) == ["ramp:2", "tx:1"]
    assert sorted(a.span.span_id for a in trace.spans_active(199)) == ["ramp:2", "tx:1"]
    assert trace.spans_active(200) == []
    assert [a.span.span_id for a in trace.spans_active(300)] == ["hold:1"]
    assert trace.spans_active(400) == []


@pytest.mark.parametrize(
    ("tick", "progress"),
    [(100, Fraction(0)), (125, Fraction(1, 4)), (150, Fraction(1, 2)), (199, Fraction(99, 100))],
)
def test_linear_progress(trace: Trace, tick: int, progress: Fraction) -> None:
    active = trace.active_span_of("packet:1", tick)
    assert active is not None
    assert active.span.span_id == "tx:1"
    assert active.progress == progress


def test_step_progress_is_zero_while_active(trace: Trace) -> None:
    for tick in (300, 350, 399):
        active = trace.active_span_of("packet:1", tick)
        assert active is not None
        assert active.span.interp == "step"
        assert active.progress == 0


@pytest.mark.parametrize(
    ("tick", "progress"),
    [
        (100, Fraction(0)),
        (125, Fraction(1, 8)),  # u=1/4: halfway to the (1/2, 1/4) key
        (150, Fraction(1, 4)),  # exactly on a key
        (175, Fraction(5, 8)),  # u=3/4: 1/4 + (1 - 1/4) * 1/2
        (199, Fraction(1, 4) + Fraction(3, 4) * Fraction(49, 50)),
    ],
)
def test_curve_progress(trace: Trace, tick: int, progress: Fraction) -> None:
    active = trace.active_span_of("packet:2", tick)
    assert active is not None
    assert active.progress == progress


def test_span_progress_curve_at_end_is_one(trace: Trace) -> None:
    ramp = next(s for s in trace.spans if s.span_id == "ramp:2")
    assert span_progress(ramp, 200) == 1


def test_span_progress_outside_curve_keys_raises() -> None:
    span = Span(
        span_id="c",
        entity="packet:1",
        kind="ramp",
        begin_tick=0,
        end_tick=10,
        outcome=Outcome.COMPLETED,
        interp="curve:x",
        keys=[(Fraction(0), Fraction(0)), (Fraction(1), Fraction(1))],
    )
    with pytest.raises(ValueError, match="outside curve keys"):
        span_progress(span, 20)


def test_active_span_of(trace: Trace) -> None:
    assert trace.active_span_of("packet:1", 99) is None
    assert trace.active_span_of("packet:1", 200) is None
    assert trace.active_span_of("node:A", 150) is None
    active = trace.active_span_of("packet:1", 150)
    assert active is not None
    assert active.span.attrs == {"via": "link:P"}


def test_active_span_of_rejects_overlapping_spans() -> None:
    w = World(MS)
    w.rec.entity_type("packet", status="str")
    w.rec.create("packet:1", status="x")
    w.rec.begin("a", "packet:1", "transit")
    w.rec.begin("b", "packet:1", "transit")
    w.now = 10
    w.rec.end("a")
    w.rec.end("b")
    trace = _trace_from(w)
    with pytest.raises(ValueError, match="2 active spans"):
        trace.active_span_of("packet:1", 5)


def test_seconds_uses_tick_rate(trace: Trace) -> None:
    assert trace.seconds(1500) == Fraction(3, 2)
    assert trace.seconds(1) == Fraction(1, 1000)


def test_spans_are_resolved_with_outcomes(trace: Trace) -> None:
    by_id = {s.span_id: s for s in trace.spans}
    assert by_id["tx:1"].outcome is Outcome.COMPLETED
    assert by_id["hold:1"].outcome is Outcome.ABORTED
    assert (by_id["tx:1"].begin_tick, by_id["tx:1"].end_tick) == (100, 200)


# -- load and tamper detection ----------------------------------------------------------

_SIM = """\
from fractions import Fraction

TICK_RATE = Fraction(1, 1000)


def run(ctx):
    rec = ctx.rec
    rec.entity_type("packet", status="str")
    rec.create("packet:1", status="in_flight")
    rec.begin("tx:1", "packet:1", "transit")
    def arrive():
        rec.end("tx:1")
        rec.set("packet:1", status="delivered")
    ctx.world.at(250, arrive)
    ctx.world.run(until=400)
"""


@pytest.fixture
def cached_trace_dir(tmp_path: Path) -> Path:
    sim = tmp_path / "sim" / "tiny.py"
    sim.parent.mkdir()
    sim.write_text(_SIM, encoding="utf-8")
    return ensure_trace(sim, {}, 0, tmp_path / "cache").directory


def test_load_round_trip(cached_trace_dir: Path) -> None:
    trace = Trace.load(cached_trace_dir)
    assert trace.meta.end_tick == 400
    assert trace.meta.event_count == len(trace.events) == 4
    assert trace.meta.span_count == len(trace.spans) == 1
    assert trace.state_at(250)["packet:1"]["status"] == "delivered"
    active = trace.active_span_of("packet:1", 125)
    assert active is not None
    assert active.progress == Fraction(1, 2)


@pytest.mark.parametrize("name", [TRACE_FILE, SPANS_FILE])
def test_load_detects_tampering(cached_trace_dir: Path, name: str) -> None:
    path = cached_trace_dir / name
    original = path.read_bytes()
    tampered = original.replace(b'"end_tick":250', b'"end_tick":251').replace(
        b'"tick":250', b'"tick":251'
    )
    assert tampered != original
    path.write_bytes(tampered)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        Trace.load(cached_trace_dir)


def test_load_detects_appended_line(cached_trace_dir: Path) -> None:
    path = cached_trace_dir / TRACE_FILE
    lines = path.read_bytes().splitlines(keepends=True)
    path.write_bytes(b"".join([*lines, lines[-1]]))
    with pytest.raises(ValueError, match="sha256 mismatch"):
        Trace.load(cached_trace_dir)


def test_last_span_of_returns_the_latest_ended_span() -> None:
    from ostrium_kinetic.sim.runner import run_simulation

    params = {
        "latency_p_s": "1.5",
        "latency_s_s": "1.2",
        "ack_latency_s": "0.6",
        "cut_p_at_s": "1.2",
        "timeout_s": "1.5",
        "reroute_delay_s": "0.1",
        "jitter_ms": 20,
        "end_s": "3.5",
    }
    root = Path(__file__).resolve().parents[2] / "examples" / "smoke-failover" / "sim"
    bundle = run_simulation(root / "failover.py", params, 7)
    from ostrium_kinetic.sim.model import Span

    spans = [Span.model_validate_json(x) for x in bundle.spans_bytes.decode().splitlines()]
    trace = Trace([], spans, bundle.meta)
    assert trace.last_span_of("packet:2", 2811) is None
    last = trace.last_span_of("packet:2", 2812)
    assert last is not None and last.attrs["via"] == "link:S" and last.outcome == "completed"
    assert trace.last_span_of("packet:1", 3000).outcome == "lost"  # type: ignore[union-attr]


# -- planned end ------------------------------------------------------------------------


def _planned_trace() -> Trace:
    """tx:cut linear 100..150 planned 200 (lost); ramp:cut curve 100..150 planned 300
    (aborted), keys (0,0) (1/2,1/4) (1,1); tx:full linear 100..200 planned 200."""
    w = World(MS)
    rec = w.rec
    rec.entity_type("packet", status="str")
    for name in ("cut", "ramp", "full"):
        rec.create(f"packet:{name}", status="in_flight")
    w.now = 100
    rec.begin("tx:cut", "packet:cut", "transit", planned_end=200)
    keys = [(0, 0), ("1/2", "1/4"), (1, 1)]
    rec.begin("ramp:cut", "packet:ramp", "ramp", interp="curve:e", keys=keys, planned_end=300)
    rec.begin("tx:full", "packet:full", "transit", planned_end=200)
    w.now = 150
    rec.end("tx:cut", Outcome.LOST)
    rec.end("ramp:cut", Outcome.ABORTED)
    w.now = 200
    rec.end("tx:full")
    return _trace_from(w)


def test_planned_progress_over_the_planned_interval() -> None:
    trace = _planned_trace()
    by_id = {s.span_id: s for s in trace.spans}
    cut, ramp, full = by_id["tx:cut"], by_id["ramp:cut"], by_id["tx:full"]
    # The actual-interval progress is unchanged (reaches 1 at the cut) ...
    assert span_progress(cut, 149) == Fraction(49, 50)
    # ... the planned progress stops halfway along.
    assert planned_progress(cut, 149) == Fraction(49, 100)
    assert stopped_progress(cut) == Fraction(1, 2)
    # Curve keys are evaluated at u over the planned interval: u = 50/200 = 1/4 -> p = 1/8.
    assert planned_progress(ramp, 150) == Fraction(1, 8)
    assert stopped_progress(ramp) == Fraction(1, 8)
    # A span that ran to its planned end: planned progress == progress, stops at 1.
    for t in (100, 137, 199):
        assert planned_progress(full, t) == span_progress(full, t)
    assert stopped_progress(full) == 1

    active = trace.active_span_of("packet:cut", 125)
    assert active is not None
    assert (active.progress, active.planned_progress) == (Fraction(1, 2), Fraction(1, 4))


def test_planned_progress_without_planned_end_equals_progress(trace: Trace) -> None:
    for t in (100, 150, 199):
        for active in trace.spans_active(t):
            assert active.span.planned_end_tick is None
            assert active.planned_progress == active.progress
    by_id = {s.span_id: s for s in trace.spans}
    assert stopped_progress(by_id["tx:1"]) == 1
    assert stopped_progress(by_id["hold:1"]) == 0  # step: no continuous progress


def test_trace_without_planned_end_is_byte_identical_to_before(cached_trace_dir: Path) -> None:
    """planned_end_tick is omitted when unset, so sims that do not use it keep their bytes."""
    spans = (cached_trace_dir / SPANS_FILE).read_bytes()
    assert spans == (
        b'{"attrs":{},"begin_tick":0,"end_tick":250,"entity":"packet:1","interp":"linear",'
        b'"kind":"transit","outcome":"completed","span_id":"tx:1"}\n'
    )
    begin = (cached_trace_dir / TRACE_FILE).read_bytes().splitlines()[1]
    assert begin == (
        b'{"entity":"packet:1","interp":"linear","kind":"transit","seq":1,"span_id":"tx:1",'
        b'"tick":0,"type":"span.begin"}'
    )
