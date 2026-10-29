from __future__ import annotations

import json
from collections.abc import Callable
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from ostrium_kinetic import __version__, canonical
from ostrium_kinetic.canonical import sha256_bytes
from ostrium_kinetic.sim import SimulationError, Trace, runner
from ostrium_kinetic.sim.runner import (
    ensure_trace,
    load_module,
    run_simulation,
    sim_source_hash,
    trace_key,
)
from ostrium_kinetic.sim.trace import META_FILE, META_SHA_FILE, SPANS_FILE, TRACE_FILE

_SIM = """\
from fractions import Fraction

TICK_RATE = Fraction(1, 1000)


def run(ctx):
    rec, world = ctx.rec, ctx.world
    rec.entity_type("counter", value="int")
    rec.create("counter:c", value=ctx.params["start"])
    jitter = ctx.rng.int_between("jitter", 0, 100)

    def bump(n):
        rec.set("counter:c", value=ctx.params["start"] + n + jitter)

    for n in range(1, 4):
        world.at(world.ticks(n), bump, n)
    world.run(until=world.ticks(ctx.params["end_s"]))
"""

PARAMS = {"start": 10, "end_s": "4"}


@pytest.fixture
def sim(tmp_path: Path) -> Path:
    path = tmp_path / "sim" / "counter.py"
    path.parent.mkdir()
    path.write_text(_SIM, encoding="utf-8")
    return path


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    return tmp_path / "cache"


# -- loading ----------------------------------------------------------------------------


def test_load_module_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_module(tmp_path / "nope.py")


@pytest.mark.parametrize(
    ("source", "missing"),
    [("def run(ctx):\n    pass\n", "TICK_RATE"), ("TICK_RATE = 1\n", "run")],
)
def test_load_module_requires_tick_rate_and_run(tmp_path: Path, source: str, missing: str) -> None:
    path = tmp_path / "bad.py"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(SimulationError, match=missing):
        load_module(path)


def test_load_module_supports_dataclasses(tmp_path: Path) -> None:
    path = tmp_path / "dc.py"
    path.write_text(
        "from __future__ import annotations\n"
        "from dataclasses import dataclass\n"
        "TICK_RATE = 1\n"
        "@dataclass\n"
        "class Box:\n"
        "    x: int\n"
        "def run(ctx):\n"
        "    pass\n",
        encoding="utf-8",
    )
    load_module(path)


# -- running ----------------------------------------------------------------------------


def test_run_simulation_is_byte_deterministic(sim: Path) -> None:
    a = run_simulation(sim, PARAMS, seed=3)
    b = run_simulation(sim, PARAMS, seed=3)
    assert a.trace_bytes == b.trace_bytes
    assert a.spans_bytes == b.spans_bytes
    assert a.meta == b.meta


def test_run_simulation_meta(sim: Path) -> None:
    bundle = run_simulation(sim, PARAMS, seed=3, display_path="sim/counter.py")
    meta = bundle.meta
    assert meta.kinetic_version == __version__
    assert meta.tick_rate == Fraction(1, 1000)
    assert meta.end_tick == 4000
    assert meta.seed == 3
    assert meta.params == PARAMS
    assert meta.sim.module == "sim/counter.py"
    assert meta.sim.source_sha256 == sim_source_hash(sim)
    assert meta.entity_types == {"counter": {"value": "int"}}
    assert meta.event_count == 4
    assert meta.span_count == 0
    assert meta.files[TRACE_FILE] == sha256_bytes(bundle.trace_bytes) == bundle.trace_sha256
    assert meta.files[SPANS_FILE] == sha256_bytes(bundle.spans_bytes)
    assert bundle.spans_bytes == b""


def test_trace_is_canonical_jsonl(sim: Path) -> None:
    bundle = run_simulation(sim, PARAMS, seed=3)
    text = bundle.trace_bytes.decode("utf-8")
    assert "\r" not in text
    assert text.endswith("\n")
    lines = text.splitlines()
    for line in lines:
        record = json.loads(line)
        assert line == json.dumps(record, sort_keys=True, separators=(",", ":"))
    order = [(json.loads(x)["tick"], json.loads(x)["seq"]) for x in lines]
    assert order == sorted(order)
    assert [s for _, s in order] == list(range(len(lines)))


def test_seed_changes_trace(sim: Path) -> None:
    assert run_simulation(sim, PARAMS, 3).trace_bytes != run_simulation(sim, PARAMS, 4).trace_bytes


# -- cache keys -------------------------------------------------------------------------


def test_trace_key_is_stable(sim: Path) -> None:
    assert trace_key(sim, PARAMS, 1) == trace_key(sim, dict(reversed(PARAMS.items())), 1)


def test_trace_key_changes_with_params_seed_and_source(sim: Path) -> None:
    base = trace_key(sim, PARAMS, 1)
    assert trace_key(sim, {**PARAMS, "start": 11}, 1) != base
    assert trace_key(sim, PARAMS, 2) != base

    sim.write_text(_SIM + "\n# edited\n", encoding="utf-8")
    edited = trace_key(sim, PARAMS, 1)
    assert edited != base

    (sim.parent / "helper.py").write_text("X = 1\n", encoding="utf-8")
    assert trace_key(sim, PARAMS, 1) != edited


def test_trace_key_ignores_line_endings_and_non_python_files(sim: Path) -> None:
    base = trace_key(sim, PARAMS, 1)
    sim.write_bytes(_SIM.replace("\n", "\r\n").encode("utf-8"))
    assert trace_key(sim, PARAMS, 1) == base
    (sim.parent / "notes.md").write_text("not code\n", encoding="utf-8")
    assert trace_key(sim, PARAMS, 1) == base


# -- ensure_trace -----------------------------------------------------------------------


def test_ensure_trace_caches(sim: Path, cache: Path) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache)
    assert not first.cached
    assert first.directory.parent == cache / "traces"
    assert first.directory.name == first.key[:16]
    for name in (TRACE_FILE, SPANS_FILE, META_FILE):
        assert (first.directory / name).is_file()

    second = ensure_trace(sim, PARAMS, 1, cache)
    assert second.cached
    assert second.directory == first.directory
    assert second.trace_sha256 == first.trace_sha256

    trace = Trace.load(second.directory)
    assert trace.meta.files[TRACE_FILE] == first.trace_sha256


def test_ensure_trace_force_reruns(sim: Path, cache: Path) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache)
    forced = ensure_trace(sim, PARAMS, 1, cache, force=True)
    assert not forced.cached
    assert forced.trace_sha256 == first.trace_sha256
    assert ensure_trace(sim, PARAMS, 1, cache).cached


def test_ensure_trace_new_key_after_param_change(sim: Path, cache: Path) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache)
    other = ensure_trace(sim, {**PARAMS, "start": 99}, 1, cache)
    assert not other.cached
    assert other.directory != first.directory
    assert other.trace_sha256 != first.trace_sha256


def test_ensure_trace_rebuilds_after_source_edit(sim: Path, cache: Path) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache)
    sim.write_text(_SIM + "\n# comment only\n", encoding="utf-8")
    second = ensure_trace(sim, PARAMS, 1, cache)
    assert not second.cached
    assert second.key != first.key
    assert second.trace_sha256 == first.trace_sha256  # same behaviour, new key


def _rewrite_meta(directory: Path, edit: Callable[[dict[str, Any]], None]) -> None:
    """Edit trace.meta.json and re-seal its sidecar, as a foreign or stale writer would."""
    meta = json.loads((directory / META_FILE).read_text("utf-8"))
    edit(meta)
    text = canonical.dumps(meta) + "\n"
    (directory / META_FILE).write_text(text, "utf-8", newline="\n")
    (directory / META_SHA_FILE).write_text(
        sha256_bytes(text.encode("utf-8")) + "\n", "utf-8", newline="\n"
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        "tamper_trace",
        "delete_meta",
        "garbage_meta",
        "delete_spans",
        "garbage_trace",
        "delete_sidecar",
        "extra_file_in_meta",
        "missing_file_in_meta",
    ],
)
def test_ensure_trace_rebuilds_corrupt_entry(sim: Path, cache: Path, corrupt: str) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache)
    d = first.directory
    if corrupt == "tamper_trace":
        data = (d / TRACE_FILE).read_bytes()
        (d / TRACE_FILE).write_bytes(data.replace(b'"value":10', b'"value":12'))
    elif corrupt == "delete_meta":
        (d / META_FILE).unlink()
    elif corrupt == "garbage_meta":
        (d / META_FILE).write_text("{not json", encoding="utf-8")
    elif corrupt == "delete_spans":
        (d / SPANS_FILE).unlink()
    elif corrupt == "garbage_trace":
        (d / TRACE_FILE).write_bytes(b"\xff\xfe garbage\n")
    elif corrupt == "delete_sidecar":
        (d / META_SHA_FILE).unlink()
    elif corrupt == "extra_file_in_meta":
        (d / "extra.txt").write_text("x", "utf-8")
        _rewrite_meta(d, lambda m: m["files"].update({"extra.txt": sha256_bytes(b"x")}))
    elif corrupt == "missing_file_in_meta":
        _rewrite_meta(d, lambda m: m["files"].pop(SPANS_FILE))

    rebuilt = ensure_trace(sim, PARAMS, 1, cache)
    assert not rebuilt.cached
    assert rebuilt.directory == d
    assert rebuilt.trace_sha256 == first.trace_sha256
    Trace.load(d)  # intact again
    assert ensure_trace(sim, PARAMS, 1, cache).cached


@pytest.mark.parametrize("corrupt", ["delete_sidecar", "extra_file_in_meta"])
def test_trace_load_rejects_unsealed_or_foreign_meta(sim: Path, cache: Path, corrupt: str) -> None:
    d = ensure_trace(sim, PARAMS, 1, cache).directory
    if corrupt == "delete_sidecar":
        (d / META_SHA_FILE).unlink()
        match = "missing"
    else:
        _rewrite_meta(d, lambda m: m["files"].update({"other.jsonl": "0" * 64}))
        match = "must list exactly"
    with pytest.raises(ValueError, match=match):
        Trace.load(d)


def test_ensure_trace_rebuilds_on_any_load_error(
    sim: Path, cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache)

    def boom(directory: Path) -> Trace:
        raise RuntimeError("unexpected")

    with monkeypatch.context() as m:
        m.setattr(runner.Trace, "load", boom)
        rebuilt = ensure_trace(sim, PARAMS, 1, cache)
    assert not rebuilt.cached
    assert rebuilt.trace_sha256 == first.trace_sha256
    assert ensure_trace(sim, PARAMS, 1, cache).cached


# -- sibling modules and cache-hit verification ----------------------------------------

_SIBLING = """\
from fractions import Fraction

TICK_RATE = Fraction(1, 1000)


def run(ctx):
    ctx.rec.entity_type("node", role="str")
    ctx.rec.create("node:A", role="{role}")
    ctx.world.run(until=1000)
"""


def test_sibling_modules_do_not_collide(tmp_path: Path, cache: Path) -> None:
    folder = tmp_path / "sims"
    folder.mkdir()
    a, b = folder / "a.py", folder / "b.py"
    a.write_text(_SIBLING.format(role="from_a"), encoding="utf-8")
    b.write_text(_SIBLING.format(role="from_b"), encoding="utf-8")
    assert sim_source_hash(a) == sim_source_hash(b)
    assert trace_key(a, {}, 1) != trace_key(b, {}, 1)

    ref_a = ensure_trace(a, {}, 1, cache, display_path="sims/a.py")
    ref_b = ensure_trace(b, {}, 1, cache, display_path="sims/b.py")
    assert not ref_b.cached
    assert ref_a.directory != ref_b.directory
    assert Trace.load(ref_a.directory).state_at(0)["node:A"]["role"] == "from_a"
    assert Trace.load(ref_b.directory).state_at(0)["node:A"]["role"] == "from_b"


@pytest.mark.parametrize(
    "edit",
    [
        lambda m: m.update(seed=2),
        lambda m: m["params"].update(start=11),
        lambda m: m["sim"].update(module="sim/other.py"),
        lambda m: m["sim"].update(source_sha256="0" * 64),
    ],
    ids=["seed", "params", "module", "source"],
)
def test_cache_hit_for_a_different_request_is_rebuilt(
    sim: Path, cache: Path, edit: Callable[[dict[str, Any]], None]
) -> None:
    first = ensure_trace(sim, PARAMS, 1, cache, display_path="sim/counter.py")
    _rewrite_meta(first.directory, edit)
    Trace.load(first.directory)  # still a well-formed, sealed trace
    again = ensure_trace(sim, PARAMS, 1, cache, display_path="sim/counter.py")
    assert not again.cached
    meta = Trace.load(again.directory).meta
    assert (meta.seed, meta.params, meta.sim.module) == (1, PARAMS, "sim/counter.py")
    assert ensure_trace(sim, PARAMS, 1, cache).cached


# -- simulations that exit --------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "match"),
    [
        ("    sys.exit(0)\n", r"simulation called exit\(0\)"),
        ("    sys.exit('bye')\n", r"simulation called exit\('bye'\)"),
        ("    raise GeneratorExit('gone')\n", "simulation raised GeneratorExit: gone"),
    ],
)
def test_run_exit_becomes_simulation_error(tmp_path: Path, body: str, match: str) -> None:
    path = tmp_path / "quits.py"
    path.write_text("import sys\nTICK_RATE = 1\ndef run(ctx):\n" + body, encoding="utf-8")
    with pytest.raises(SimulationError, match=match):
        run_simulation(path, {}, 0)


def test_exit_at_import_becomes_simulation_error(tmp_path: Path) -> None:
    path = tmp_path / "quits_on_import.py"
    path.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    with pytest.raises(SimulationError, match=r"simulation called exit\(3\)"):
        load_module(path)


def test_keyboard_interrupt_still_propagates(tmp_path: Path) -> None:
    path = tmp_path / "ctrl_c.py"
    path.write_text("TICK_RATE = 1\ndef run(ctx):\n    raise KeyboardInterrupt\n", "utf-8")
    with pytest.raises(KeyboardInterrupt):
        run_simulation(path, {}, 0)


def test_ensure_trace_leaves_no_temp_dirs(sim: Path, cache: Path) -> None:
    ensure_trace(sim, PARAMS, 1, cache)
    ensure_trace(sim, PARAMS, 1, cache, force=True)
    leftovers = [p.name for p in (cache / "traces").iterdir() if p.name.startswith(".tmp-")]
    assert leftovers == []


def test_tampered_meta_is_detected_and_rebuilt(sim: Path, tmp_path: Path) -> None:
    from ostrium_kinetic.sim.trace import META_FILE

    cache = tmp_path / "cache"
    first = ensure_trace(sim, PARAMS, seed=3, cache_root=cache)
    meta = first.directory / META_FILE
    text = meta.read_text("utf-8")
    meta.write_text(text.replace('"seed":3', '"seed":4'), "utf-8", newline="\n")
    with pytest.raises(ValueError, match="meta was modified"):
        Trace.load(first.directory)
    again = ensure_trace(sim, PARAMS, seed=3, cache_root=cache)
    assert again.cached is False
    Trace.load(again.directory)
