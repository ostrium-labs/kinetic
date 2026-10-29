"""Running a film's simulations into the content-addressed trace cache."""

from __future__ import annotations

from pathlib import Path

from ostrium_kinetic import canonical
from ostrium_kinetic.contract.loader import LoadedFilm
from ostrium_kinetic.contract.models import SimRef
from ostrium_kinetic.sim.runner import TraceRef, ensure_trace
from ostrium_kinetic.sim.world import SimulationError
from ostrium_kinetic.workspace import Workspace


def sim_module_path(loaded: LoadedFilm, sim: SimRef) -> Path:
    return (loaded.film_dir / sim.module).resolve()


def sim_display_path(loaded: LoadedFilm, sim: SimRef) -> str:
    """The module path as recorded in trace metadata: relative to the film, POSIX."""
    path = sim_module_path(loaded, sim)
    try:
        return path.relative_to(loaded.film_dir).as_posix()
    except ValueError:
        return Path(sim.module).as_posix()


def _identity(loaded: LoadedFilm, sim: SimRef) -> str:
    return canonical.sha256_json(
        {"module": str(sim_module_path(loaded, sim)), "params": sim.params, "seed": sim.seed}
    )


class SimCache:
    """Runs each distinct (module, params, seed) once per process, however many shots use it."""

    def __init__(self, loaded: LoadedFilm, ws: Workspace, force: bool = False) -> None:
        self.loaded = loaded
        self.ws = ws
        self.force = force
        self._refs: dict[str, TraceRef] = {}

    def trace_for(self, sim: SimRef) -> TraceRef:
        key = _identity(self.loaded, sim)
        if key not in self._refs:
            self._refs[key] = ensure_trace(
                sim_module_path(self.loaded, sim),
                sim.params,
                sim.seed,
                self.ws.cache_dir,
                display_path=sim_display_path(self.loaded, sim),
                force=self.force,
            )
        return self._refs[key]


def run_film_sims(
    loaded: LoadedFilm, ws: Workspace, shot_id: str | None = None, force: bool = False
) -> list[tuple[str, TraceRef]]:
    """Ensure a cached trace for every shot with a simulation (or just `shot_id`).

    Raises KeyError for an unknown shot id and ValueError when a simulation fails.
    """
    shots = [shot for _, shot in loaded.film.shots()]
    if shot_id is not None:
        shots = [shot for shot in shots if shot.id == shot_id]
        if not shots:
            raise KeyError(f"no shot {shot_id!r} in {loaded.film.id}")
    cache = SimCache(loaded, ws, force=force)
    results: list[tuple[str, TraceRef]] = []
    for shot in shots:
        if shot.sim is None:
            continue
        try:
            results.append((shot.id, cache.trace_for(shot.sim)))
        except SimulationError as exc:
            raise ValueError(f"shot {shot.id}: simulation broke the trace contract: {exc}") from exc
        except Exception as exc:  # a simulation is user code: report any failure per shot
            raise ValueError(
                f"shot {shot.id}: simulation failed: {type(exc).__name__}: {exc}"
            ) from exc
    return results
