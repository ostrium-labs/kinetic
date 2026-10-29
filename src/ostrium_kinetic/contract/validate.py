"""Film validation beyond the schema: cross-references, evidence rules, files, checkpoints."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any, Literal

from ostrium_kinetic.contract.loader import LoadedFilm
from ostrium_kinetic.contract.models import QUANTITATIVE, Evidence, Expectation, Shot
from ostrium_kinetic.sim.trace import Trace
from ostrium_kinetic.sim.world import SimulationError
from ostrium_kinetic.timeline import ShotClock, seconds_to_ticks
from ostrium_kinetic.workspace import Workspace

_MISSING = object()


@dataclass(frozen=True)
class Diagnostic:
    level: Literal["error", "warning"]
    where: str
    message: str


def _error(where: str, message: str) -> Diagnostic:
    return Diagnostic("error", where, message)


def _warning(where: str, message: str) -> Diagnostic:
    return Diagnostic("warning", where, message)


def _duplicates(kind: str, ids: Iterable[str]) -> list[Diagnostic]:
    counts = Counter(ids)
    return [
        _error(f"{kind} {i}", f"id {i!r} is used {n} times") for i, n in counts.items() if n > 1
    ]


def _speed(shot: Shot, fps: Fraction) -> Fraction | None:
    tm = shot.timemap
    if tm is None or tm.sim_end_s <= tm.sim_start_s:
        return None
    return (tm.sim_end_s - tm.sim_start_s) / (Fraction(shot.frames) / fps)


def _check_static(loaded: LoadedFilm) -> list[Diagnostic]:
    film = loaded.film
    out: list[Diagnostic] = []
    shots = film.shots()

    out += _duplicates("sequence/shot", [s.id for s in film.sequences] + [s.id for _, s in shots])
    out += _duplicates("claim", [c.id for c in loaded.claims])
    out += _duplicates("source", [s.id for s in film.sources])
    out += _duplicates("narration", [n.id for n in film.narration])
    out += _duplicates("assumption", [a.id for a in film.assumptions])

    source_ids = {s.id for s in film.sources}
    claims = {c.id: c for c in loaded.claims}
    narration_ids = {n.id for n in film.narration}

    for claim in loaded.claims:
        if claim.source is not None and claim.source not in source_ids:
            out.append(_error(f"claim {claim.id}", f"unknown source {claim.source!r}"))

    for _, shot in shots:
        where = f"shot {shot.id}"
        for claim_id in shot.claims:
            if claim_id not in claims:
                out.append(_error(where, f"unknown claim {claim_id!r}"))
        if shot.narration is not None and shot.narration not in narration_ids:
            out.append(_error(where, f"unknown narration segment {shot.narration!r}"))

        if shot.evidence is Evidence.TRACE:
            out.append(
                _error(
                    where,
                    "evidence 'trace' needs a recorded trace; recorded traces arrive "
                    "in a later milestone",
                )
            )
        if shot.evidence is Evidence.SIMULATED and shot.sim is None:
            out.append(_error(where, "a simulated shot needs `sim`"))
        if shot.evidence is Evidence.ILLUSTRATIVE:
            if shot.sim is not None:
                out.append(_error(where, "an illustrative shot must not have `sim`"))
            for claim_id in shot.claims:
                claim = claims.get(claim_id)
                if claim is not None and claim.evidence in QUANTITATIVE:
                    out.append(
                        _warning(
                            where,
                            f"illustrative shot references {claim.evidence.value} claim "
                            f"{claim_id!r}; it may show the claim's text but no quantities",
                        )
                    )

        if shot.sim is not None and shot.timemap is None:
            out.append(_error(where, "a shot with `sim` needs a `timemap`"))
        if shot.sim is None and shot.timemap is not None:
            out.append(_warning(where, "`timemap` has no effect without `sim`"))
        if shot.timemap is not None:
            tm = shot.timemap
            if tm.sim_end_s <= tm.sim_start_s:
                out.append(_error(where, "timemap: sim_end_s must be greater than sim_start_s"))
            if tm.sim_start_s < 0:
                out.append(_error(where, "timemap: sim_start_s must be >= 0"))
            speed = _speed(shot, film.fps)
            if speed is not None and speed != 1 and not (tm.label and tm.label.strip()):
                out.append(
                    _error(
                        where,
                        f"timemap runs at {float(speed):.3g}x real time ({speed}); a non-empty "
                        "`label` is required so the film says time is compressed or slowed",
                    )
                )

        if shot.sim is not None and not (loaded.film_dir / shot.sim.module).is_file():
            out.append(_error(where, f"simulation module not found: {shot.sim.module}"))
        if not (loaded.film_dir / shot.scene).is_file():
            out.append(_warning(where, f"scene file not found: {shot.scene}"))

        for cp in shot.checkpoints:
            if cp.at_frame >= shot.frames:
                out.append(
                    _error(
                        f"{where} @frame {cp.at_frame}",
                        f"checkpoint frame is outside the shot ({shot.frames} frames)",
                    )
                )
    return out


def _span_attr(span: Any, attr: str) -> Any:
    if attr in ("span_id", "kind", "interp", "begin_tick", "end_tick"):
        return getattr(span, attr)
    if attr == "outcome":
        return span.outcome.value
    return span.attrs.get(attr, _MISSING)


def _actual(trace: Trace, exp: Expectation, tick: int) -> Any:
    if exp.field == "active_span" or exp.field.startswith("active_span."):
        active = trace.active_span_of(exp.entity, tick)
        if exp.field == "active_span":
            return None if active is None else active.span.span_id
        if active is None:
            return _MISSING
        return _span_attr(active.span, exp.field.removeprefix("active_span."))
    state = trace.state_at(tick).get(exp.entity)
    if state is None:
        return _MISSING
    return state.get(exp.field, _MISSING)


def _show(value: Any) -> str:
    return "<absent>" if value is _MISSING else repr(value)


def _matches(actual: Any, expected: Any) -> bool:
    if actual is _MISSING:
        return False
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    return actual == expected


def _check_sims(loaded: LoadedFilm, ws: Workspace) -> list[Diagnostic]:
    # Imported here: plan.simulate depends on the contract package.
    from ostrium_kinetic.plan.simulate import SimCache

    film = loaded.film
    cache = SimCache(loaded, ws)
    traces: dict[Path, Trace] = {}
    out: list[Diagnostic] = []
    for _, shot in film.shots():
        where = f"shot {shot.id}"
        sim, tm = shot.sim, shot.timemap
        if sim is None or tm is None or shot.evidence is Evidence.TRACE:
            continue
        if not (loaded.film_dir / sim.module).is_file():
            continue  # already reported
        try:
            ref = cache.trace_for(sim)
            if ref.directory not in traces:
                traces[ref.directory] = Trace.load(ref.directory)
        except SimulationError as exc:
            out.append(_error(where, f"simulation broke the trace contract: {exc}"))
            continue
        except Exception as exc:  # simulations are user code
            out.append(_error(where, f"simulation failed: {type(exc).__name__}: {exc}"))
            continue
        trace = traces[ref.directory]
        meta = trace.meta
        try:
            start = seconds_to_ticks(tm.sim_start_s, meta.tick_rate)
            end = seconds_to_ticks(tm.sim_end_s, meta.tick_rate)
        except ValueError as exc:
            out.append(_error(where, f"timemap: {exc}"))
            continue
        if start < 0 or end > meta.end_tick or end <= start:
            out.append(
                _error(
                    where,
                    f"timemap {tm.sim_start_s}..{tm.sim_end_s} s (ticks {start}..{end}) is not "
                    f"within the trace [0, {meta.end_tick}]",
                )
            )
            continue
        clock = ShotClock(shot.frames, film.fps, start, end, meta.tick_rate)
        for cp in shot.checkpoints:
            if cp.at_frame >= shot.frames:
                continue  # already reported
            tick = clock.tick_at(cp.at_frame)
            for exp in cp.expect:
                try:
                    actual = _actual(trace, exp, tick)
                except ValueError as exc:
                    out.append(_error(f"{where} @frame {cp.at_frame}", str(exc)))
                    continue
                if not _matches(actual, exp.equals):
                    out.append(
                        _error(
                            f"{where} @frame {cp.at_frame}",
                            f"checkpoint failed at tick {tick}: {exp.entity} {exp.field} "
                            f"expected {exp.equals!r}, got {_show(actual)}",
                        )
                    )
    return out


def validate_film(loaded: LoadedFilm, ws: Workspace, run_sims: bool = True) -> list[Diagnostic]:
    """All diagnostics for a loaded film. Errors block `plan`; warnings do not."""
    out = _check_static(loaded)
    if run_sims:
        out += _check_sims(loaded, ws)
    return out
