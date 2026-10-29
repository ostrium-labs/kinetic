"""The GDScript trace sampler (renderers/godot/.../trace.gd) matches sim/trace.py exactly."""

from __future__ import annotations

import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from ostrium_kinetic import canonical
from ostrium_kinetic.adapters.godot import RUNTIME_DIR
from ostrium_kinetic.env.tools import discover, resolve
from ostrium_kinetic.sim import Outcome, Trace, TraceMeta, World
from ostrium_kinetic.sim.model import SimSource
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.sim.trace import (
    META_FILE,
    META_SHA_FILE,
    SPANS_FILE,
    TRACE_FILE,
    ActiveSpan,
    encode_jsonl,
)

_FOUND = resolve(discover("godot", None, probe=False))
GODOT = Path(_FOUND.path) if _FOUND else None

pytestmark = [
    pytest.mark.renderer("godot"),
    pytest.mark.skipif(GODOT is None, reason="Godot not found (kinetic doctor)"),
]

SAMPLER = "res://addons/kinetic_runtime/tools/sample_trace.gd"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A copy of the runtime project, so Godot's .godot/ cache lands in tmp."""
    dest = tmp_path_factory.mktemp("godot") / "project"
    shutil.copytree(RUNTIME_DIR, dest, ignore=shutil.ignore_patterns(".godot"))
    return dest


def _sample(project: Path, trace_dir: Path, ticks: list[int]) -> dict[str, Any]:
    assert GODOT is not None
    args = [
        str(GODOT),
        "--headless",
        "--path",
        str(project),
        "--script",
        SAMPLER,
        "--",
        "--trace",
        str(trace_dir),
        "--ticks",
        ",".join(str(t) for t in ticks),
    ]
    proc = subprocess.run(
        args, capture_output=True, text=True, encoding="utf-8", timeout=120, check=False
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SCRIPT ERROR" not in proc.stdout + proc.stderr, proc.stdout + proc.stderr
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("KINETIC_SAMPLE "))
    return json.loads(line.removeprefix("KINETIC_SAMPLE "))


def _expected_span(active: ActiveSpan) -> dict[str, Any]:
    span = active.span
    return {
        **span.attrs,
        "span_id": span.span_id,
        "kind": span.kind,
        "interp": span.interp,
        "outcome": span.outcome.value,
        "begin_tick": span.begin_tick,
        "end_tick": span.end_tick,
        "entity": span.entity,
        "progress": float(active.progress),
    }


def _assert_parity(trace: Trace, sampled: dict[str, Any], ticks: list[int]) -> None:
    assert {int(t) for t in sampled} == set(ticks)
    for t in ticks:
        got = sampled[str(t)]
        want_state = trace.state_at(t)
        assert got["state"] == want_state, f"state at tick {t}"
        # Types survive too: integers stay integers.
        for entity, fields in want_state.items():
            for key, value in fields.items():
                assert type(got["state"][entity][key]) is type(value), (t, entity, key)
        want_spans = [_expected_span(a) for a in trace.spans_active(t)]
        assert len(got["spans"]) == len(want_spans), f"spans at tick {t}"
        for g, w in zip(got["spans"], want_spans, strict=True):
            assert g["progress"] == pytest.approx(w["progress"], abs=1e-9), (t, w["span_id"])
            assert {k: v for k, v in g.items() if k != "progress"} == {
                k: v for k, v in w.items() if k != "progress"
            }


def test_smoke_failover_parity(
    project: Path,
    tmp_path: Path,
    failover_module: Path,
    failover_params: dict[str, Any],
) -> None:
    ref = ensure_trace(failover_module, failover_params, 7, tmp_path / "cache")
    trace = Trace.load(ref.directory)
    boundaries = {0, trace.meta.end_tick}
    for span in trace.spans:
        boundaries |= {span.begin_tick - 1, span.begin_tick, span.end_tick - 1, span.end_tick}
    for event in trace.events:
        boundaries |= {event.tick - 1, event.tick, event.tick + 1}
    extra = [333, 700, 1000, 1350, 2000, 2500, 3100, 3466, 9999]
    ticks = [*sorted(t for t in boundaries if t >= 0), *extra]
    assert len(ticks) >= 25
    _assert_parity(trace, _sample(project, ref.directory, ticks), ticks)


def _write_trace(world: World, directory: Path) -> Trace:
    spans = world.rec.finish()
    trace_bytes = encode_jsonl(world.rec.events)
    spans_bytes = encode_jsonl(spans)
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
        files={
            TRACE_FILE: canonical.sha256_bytes(trace_bytes),
            SPANS_FILE: canonical.sha256_bytes(spans_bytes),
        },
    )
    directory.mkdir(parents=True)
    (directory / TRACE_FILE).write_bytes(trace_bytes)
    (directory / SPANS_FILE).write_bytes(spans_bytes)
    meta_text = canonical.dumps(meta.model_dump(mode="json")) + "\n"
    canonical.write_text_lf(directory / META_FILE, meta_text)
    canonical.write_text_lf(
        directory / META_SHA_FILE, canonical.sha256_bytes(meta_text.encode("utf-8")) + "\n"
    )
    return Trace.load(directory)


def test_step_curve_destroy_and_types_parity(project: Path, tmp_path: Path) -> None:
    w = World(Fraction(1, 1000))
    rec = w.rec
    rec.entity_type("node", status="str", load="int", temp="float", ok="bool")
    rec.entity_type("packet", status="str")
    rec.create("node:A", status="up", load=0, temp=0.25, ok=True)
    rec.create("packet:1", status="queued")
    rec.create("packet:2", status="queued")
    w.now = 10
    rec.set("node:A", load=5)
    rec.set("node:A", status='busy "quoted" é', load=7, temp=1.5, ok=False)
    w.now = 20
    rec.destroy("node:A")
    w.now = 100
    rec.begin("tx:1", "packet:1", "transit", via="link:P", size=3, weight=0.5)
    rec.begin(
        "ramp:2", "packet:2", "ramp", interp="curve:ease", keys=[(0, 0), ("1/3", "1/4"), (1, 1)]
    )
    w.now = 197
    rec.end("tx:1", Outcome.LOST)
    rec.end("ramp:2")
    w.now = 300
    rec.begin("hold:1", "packet:1", "hold", interp="step")
    w.now = 400
    rec.end("hold:1")
    trace = _write_trace(w, tmp_path / "trace")

    ticks = [0, 5, 9, 10, 11, 19, 20, 21, 99, 100, 101, 131, 132, 133, 150, 196, 197, 299, 300]
    ticks += [350, 399, 400, 401]
    _assert_parity(trace, _sample(project, tmp_path / "trace", ticks), ticks)
