"""Loading a simulation module by path, running it, and caching its trace by content."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import ModuleType
from typing import Any

from ostrium_kinetic import __version__, canonical
from ostrium_kinetic.canonical import to_fraction
from ostrium_kinetic.sim.model import SimSource, TraceMeta
from ostrium_kinetic.sim.rng import RngStreams
from ostrium_kinetic.sim.trace import (
    META_FILE,
    META_SHA_FILE,
    SPANS_FILE,
    TRACE_FILE,
    Trace,
    encode_jsonl,
)
from ostrium_kinetic.sim.world import SimContext, SimulationError, World


@dataclass(frozen=True)
class TraceBundle:
    trace_bytes: bytes
    spans_bytes: bytes
    meta: TraceMeta

    @property
    def trace_sha256(self) -> str:
        return self.meta.files[TRACE_FILE]


@dataclass(frozen=True)
class TraceRef:
    key: str
    directory: Path
    trace_sha256: str
    cached: bool


def sim_source_hash(module_path: Path) -> str:
    """Hash every .py file in the simulation's directory, so editing a helper module next
    to the simulation also invalidates its traces."""
    root = module_path.parent
    return canonical.sha256_tree(
        root, [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    )


def _call_user_code(fn: Callable[[], object]) -> None:
    """Run simulation code so that `sys.exit()` or another non-Exception BaseException
    becomes a SimulationError instead of terminating Kinetic. Ctrl-C still propagates."""
    try:
        fn()
    except KeyboardInterrupt:
        raise
    except Exception:
        raise
    except SystemExit as exc:
        raise SimulationError(f"simulation called exit({exc.code!r})") from exc
    except BaseException as exc:
        raise SimulationError(f"simulation raised {type(exc).__name__}: {exc}") from exc


def load_module(module_path: Path) -> ModuleType:
    module_path = module_path.resolve()
    if not module_path.is_file():
        raise FileNotFoundError(f"simulation module not found: {module_path}")
    name = "kinetic_sim_" + canonical.sha256_bytes(str(module_path).encode())[:12]
    spec = importlib.util.spec_from_file_location(name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {module_path}")
    loader = spec.loader
    module = importlib.util.module_from_spec(spec)
    # dataclasses and typing resolve annotations through sys.modules[module.__name__].
    sys.modules[name] = module
    try:
        _call_user_code(lambda: loader.exec_module(module))
    except BaseException:
        sys.modules.pop(name, None)
        raise
    for attr in ("TICK_RATE", "run"):
        if not hasattr(module, attr):
            raise SimulationError(f"{module_path.name} must define {attr}")
    return module


def run_simulation(
    module_path: Path, params: Mapping[str, Any], seed: int, display_path: str | None = None
) -> TraceBundle:
    module = load_module(module_path)
    world = World(to_fraction(module.TICK_RATE))
    ctx = SimContext(params=dict(params), rng=RngStreams(seed), world=world)
    _call_user_code(lambda: module.run(ctx))
    spans = world.rec.finish()
    trace_bytes = encode_jsonl(world.rec.events)
    spans_bytes = encode_jsonl(spans)
    meta = TraceMeta(
        kinetic_version=__version__,
        tick_rate=world.tick_rate,
        end_tick=world.now,
        seed=seed,
        params=dict(params),
        sim=SimSource(
            module=display_path or module_path.name, source_sha256=sim_source_hash(module_path)
        ),
        entity_types=world.rec.entity_types,
        event_count=len(world.rec.events),
        span_count=len(spans),
        files={
            TRACE_FILE: canonical.sha256_bytes(trace_bytes),
            SPANS_FILE: canonical.sha256_bytes(spans_bytes),
        },
    )
    return TraceBundle(trace_bytes, spans_bytes, meta)


def trace_key(module_path: Path, params: Mapping[str, Any], seed: int) -> str:
    """The source hash covers every .py file in the simulation's directory, so the entry
    module's file name is part of the key too: sibling simulations in one folder share a
    source hash but are different simulations."""
    return canonical.sha256_json(
        {
            "contract": "0.1",
            "kinetic": __version__,
            "sim": sim_source_hash(module_path),
            "entry": module_path.name,
            "params": dict(params),
            "seed": seed,
        }
    )


def _matches_request(
    meta: TraceMeta, module_path: Path, params: Mapping[str, Any], seed: int
) -> bool:
    """A cache hit must be the trace of this exact request; anything else (a prefix
    collision, a stale or foreign entry) is rebuilt."""
    return (
        PurePosixPath(meta.sim.module).name == module_path.name
        and meta.sim.source_sha256 == sim_source_hash(module_path)
        and canonical.dumps(meta.params) == canonical.dumps(dict(params))
        and meta.seed == seed
    )


def ensure_trace(
    module_path: Path,
    params: Mapping[str, Any],
    seed: int,
    cache_root: Path,
    display_path: str | None = None,
    force: bool = False,
) -> TraceRef:
    """Run the simulation unless an intact cached trace for the same request exists."""
    key = trace_key(module_path, params, seed)
    directory = cache_root / "traces" / key[:16]
    if directory.exists() and not force:
        try:
            trace = Trace.load(directory)
            if _matches_request(trace.meta, module_path, params, seed):
                return TraceRef(key, directory, trace.meta.files[TRACE_FILE], cached=True)
        except Exception:  # any failure to read a cache entry means it is broken
            pass
        shutil.rmtree(directory, ignore_errors=True)  # broken or foreign entry: rebuild

    bundle = run_simulation(module_path, params, seed, display_path)
    directory.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".tmp-", dir=directory.parent))
    try:
        (tmp / TRACE_FILE).write_bytes(bundle.trace_bytes)
        (tmp / SPANS_FILE).write_bytes(bundle.spans_bytes)
        meta_text = canonical.dumps(bundle.meta.model_dump(mode="json")) + "\n"
        canonical.write_text_lf(tmp / META_FILE, meta_text)
        canonical.write_text_lf(
            tmp / META_SHA_FILE, canonical.sha256_bytes(meta_text.encode("utf-8")) + "\n"
        )
        if directory.exists():
            shutil.rmtree(directory)
        os.replace(tmp, directory)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return TraceRef(key, directory, bundle.trace_sha256, cached=False)
