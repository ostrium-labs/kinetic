"""The smoke-failover example: its event story, its golden trace hash, and its invariants."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from ostrium_kinetic.canonical import sha256_bytes
from ostrium_kinetic.sim import Outcome, Trace
from ostrium_kinetic.sim.runner import TraceBundle, ensure_trace, run_simulation
from ostrium_kinetic.sim.trace import SPANS_FILE, TRACE_FILE
from ostrium_kinetic.timeline import ShotClock

SEED = 7
BAD_LINK_STATES = {"failed", "down"}


@pytest.fixture(scope="module")
def bundle(failover_module: Path, failover_params: dict[str, Any]) -> TraceBundle:
    return run_simulation(failover_module, failover_params, SEED)


@pytest.fixture(scope="module")
def trace(bundle: TraceBundle) -> Trace:
    events = [json.loads(line) for line in bundle.trace_bytes.decode().splitlines()]
    spans = [json.loads(line) for line in bundle.spans_bytes.decode().splitlines()]
    from ostrium_kinetic.sim import Span, TraceEvent

    return Trace(
        [TraceEvent.model_validate(e) for e in events],
        [Span.model_validate(s) for s in spans],
        bundle.meta,
    )


def _signals(trace: Trace, name: str) -> list[int]:
    return [e.tick for e in trace.events if e.type == "signal" and e.name == name]


def _span(trace: Trace, span_id: str) -> Any:
    return next(s for s in trace.spans if s.span_id == span_id)


# -- the story --------------------------------------------------------------------------


def test_primary_cut_at_1200_loses_packet_1(trace: Trace) -> None:
    assert _signals(trace, "fault.injected") == [1200]
    tx1 = _span(trace, "tx:packet:1")
    assert (tx1.begin_tick, tx1.end_tick) == (0, 1200)
    assert tx1.outcome is Outcome.LOST
    assert tx1.attrs["via"] == "link:P"
    assert trace.state_at(1199)["link:P"]["status"] == "up"
    assert trace.state_at(1200)["link:P"]["status"] == "failed"
    assert trace.state_at(1200)["packet:1"]["status"] == "lost"


def test_timeout_at_1500_then_reroute_at_1600(trace: Trace) -> None:
    assert _signals(trace, "timeout.fired") == [1500]
    assert _signals(trace, "route.changed") == [1600]
    state = trace.state_at(1600)
    assert state["link:P"]["status"] == "down"
    assert state["link:S"]["status"] == "up"
    assert trace.state_at(1599)["link:S"]["status"] == "standby"


def test_packet_2_delivered_over_secondary(trace: Trace) -> None:
    tx2 = _span(trace, "tx:packet:2")
    assert tx2.begin_tick == 1600
    assert tx2.end_tick == 1600 + 1212  # 1.2 s + 12 ms jitter at seed 7
    assert tx2.outcome is Outcome.COMPLETED
    assert tx2.attrs == {"from": "node:A", "to": "node:B", "via": "link:S"}
    assert trace.state_at(tx2.end_tick)["packet:2"]["status"] == "delivered"


def test_ack_delivered_before_end(trace: Trace) -> None:
    ack = _span(trace, "tx:packet:ack2")
    assert ack.outcome is Outcome.COMPLETED
    assert ack.attrs["via"] == "link:S"
    assert ack.begin_tick == _span(trace, "tx:packet:2").end_tick
    assert ack.end_tick - ack.begin_tick == 600
    assert _signals(trace, "ack.received") == [ack.end_tick]
    assert ack.end_tick < trace.meta.end_tick == 3500


def test_exactly_three_spans_and_jittered_link_latencies(trace: Trace) -> None:
    assert [s.span_id for s in trace.spans] == ["tx:packet:1", "tx:packet:2", "tx:packet:ack2"]
    links = trace.state_at(0)
    assert links["link:P"]["latency_ms"] == 1488
    assert links["link:S"]["latency_ms"] == 1212


def test_smoke_checkpoints_at_30fps(trace: Trace) -> None:
    """The plan's smoke checkpoints (sec. 11), sampled through a 105-frame real-time clock.
    With seed 7, packet 2 lands at tick 2812, so "delivered" holds from frame 85, not 84."""
    clock = ShotClock(105, Fraction(30), 0, 3500, trace.meta.tick_rate)

    tick = clock.tick_at(30)
    on_p = trace.active_span_of("packet:1", tick)
    assert on_p is not None and on_p.span.attrs["via"] == "link:P"

    assert trace.state_at(clock.tick_at(40))["link:P"]["status"] == "failed"

    on_s = trace.active_span_of("packet:2", clock.tick_at(60))
    assert on_s is not None and on_s.span.attrs["via"] == "link:S"

    assert trace.state_at(clock.tick_at(84))["packet:2"]["status"] == "in_flight"
    for frame in range(85, 105):
        assert trace.state_at(clock.tick_at(frame))["packet:2"]["status"] == "delivered"


# -- determinism ------------------------------------------------------------------------


def test_trace_matches_golden_sha(bundle: TraceBundle, golden_dir: Path) -> None:
    golden = (golden_dir / "smoke-failover.trace.sha256").read_text("utf-8").strip()
    assert sha256_bytes(bundle.trace_bytes) == bundle.trace_sha256 == golden


def test_spans_match_golden_sha(bundle: TraceBundle, golden_dir: Path) -> None:
    golden = (golden_dir / "smoke-failover.spans.sha256").read_text("utf-8").strip()
    assert sha256_bytes(bundle.spans_bytes) == bundle.meta.files[SPANS_FILE] == golden


def test_rerun_is_byte_identical(
    bundle: TraceBundle, failover_module: Path, failover_params: dict[str, Any]
) -> None:
    again = run_simulation(failover_module, failover_params, SEED)
    assert again.trace_bytes == bundle.trace_bytes
    assert again.spans_bytes == bundle.spans_bytes


def test_cached_trace_on_disk_matches_golden(
    tmp_path: Path, failover_module: Path, failover_params: dict[str, Any], golden_dir: Path
) -> None:
    ref = ensure_trace(failover_module, failover_params, SEED, tmp_path / "cache")
    golden = (golden_dir / "smoke-failover.trace.sha256").read_text("utf-8").strip()
    assert sha256_bytes((ref.directory / TRACE_FILE).read_bytes()) == golden
    golden_spans = (golden_dir / "smoke-failover.spans.sha256").read_text("utf-8").strip()
    assert sha256_bytes((ref.directory / SPANS_FILE).read_bytes()) == golden_spans
    assert Trace.load(ref.directory).meta.seed == SEED


# -- invariants over seeds and jitter ---------------------------------------------------


def _check_invariants(bundle: TraceBundle) -> None:
    events = [json.loads(line) for line in bundle.trace_bytes.decode().splitlines()]
    links: dict[str, str] = {}
    open_spans: set[str] = set()
    begun: list[str] = []
    for e in events:
        if e["entity"].startswith("link:") and "state" in e and "status" in e["state"]:
            links[e["entity"]] = e["state"]["status"]
        if e["type"] == "span.begin":
            via = e["attrs"]["via"]
            assert links[via] not in BAD_LINK_STATES, f"transit began on {via} ({links[via]})"
            open_spans.add(e["span_id"])
            begun.append(e["span_id"])
        elif e["type"] == "span.end":
            open_spans.remove(e["span_id"])
    assert not open_spans

    spans = [json.loads(line) for line in bundle.spans_bytes.decode().splitlines()]
    assert sorted(s["span_id"] for s in spans) == sorted(begun)
    assert all(s["end_tick"] > s["begin_tick"] for s in spans)
    assert all(s["end_tick"] <= bundle.meta.end_tick for s in spans)
    by_id = {s["span_id"]: s for s in spans}
    assert by_id["tx:packet:1"]["outcome"] == "lost"
    assert by_id["tx:packet:2"]["outcome"] == "completed"
    assert by_id["tx:packet:ack2"]["outcome"] == "completed"


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    seed=st.integers(min_value=0, max_value=10_000), jitter=st.integers(min_value=0, max_value=50)
)
def test_never_transit_on_failed_link_and_all_spans_closed(
    failover_module: Path, failover_params: dict[str, Any], seed: int, jitter: int
) -> None:
    params = {**failover_params, "jitter_ms": jitter}
    _check_invariants(run_simulation(failover_module, params, seed))
