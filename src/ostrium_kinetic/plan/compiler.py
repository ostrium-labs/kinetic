"""Compiling a validated film into render-plan.json, with a content-addressed key per shot."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from ostrium_kinetic import CONTRACT_VERSION, __version__, canonical
from ostrium_kinetic.canonical import Exact
from ostrium_kinetic.contract.loader import LoadedFilm
from ostrium_kinetic.contract.models import PROFILES, Aspect, Evidence, Profile, Shot
from ostrium_kinetic.contract.validate import validate_film
from ostrium_kinetic.plan.simulate import run_film_sims
from ostrium_kinetic.sim.model import TraceMeta
from ostrium_kinetic.sim.runner import TraceRef
from ostrium_kinetic.sim.trace import META_FILE
from ostrium_kinetic.timeline import seconds_to_ticks
from ostrium_kinetic.workspace import Workspace

PLAN_FILE = "render-plan.json"


class PlanError(Exception):
    """The film cannot be planned for this profile."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShotPlan(_Model):
    shot_id: str
    sequence_id: str
    renderer: Literal["godot", "blender", "manim"]
    scene: str
    framing: str  # aspect key into shot.framings, e.g. "16:9"
    camera: str
    frame_start: int  # first frame of this shot in the cut for this profile
    frame_count: int
    fps: Exact
    width: int
    height: int
    evidence: Evidence
    trace_dir: str | None = None
    trace_sha256: str | None = None
    tick_start: int | None = None
    tick_end: int | None = None
    scene_sha256: str | None = None
    cache_key: str


class RenderPlan(_Model):
    contract: Literal["0.1"] = "0.1"
    kinetic_version: str
    film_id: str
    profile: str
    width: int
    height: int
    fps: Exact
    total_frames: int
    shots: list[ShotPlan]
    film_sha256: str


def _rel(path: Path, root: Path) -> str:
    """`path` relative to the workspace root, POSIX. A trace cache configured outside the
    workspace has no relative form, so the plan then records the absolute path and its
    bytes are machine-specific. That is tolerated because `trace_dir` is only a locator:
    it is never part of a cache key (the trace is identified by `trace_sha256`)."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def scene_tree_sha(scene_path: Path, film_dir: Path, cache_dir: Path) -> str | None:
    """Hash of the scene's directory (scripts, sub-scenes and assets next to the scene file),
    so editing a helper changes the key. A scene directly in the film directory hashes only
    itself: the film directory holds film.yaml and must not feed every shot's key."""
    scene_dir = scene_path.parent.resolve()
    if not scene_dir.is_dir():
        return None
    if scene_dir == film_dir.resolve():
        return canonical.sha256_file(scene_path) if scene_path.is_file() else None
    cache = cache_dir.resolve()
    files = [
        p
        for p in scene_dir.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not p.resolve().is_relative_to(cache)
    ]
    return canonical.sha256_tree(scene_dir, files)


def style_sha(loaded: LoadedFilm) -> str | None:
    style = loaded.film.style
    if style is None:
        return None
    path = loaded.film_dir / style
    return canonical.sha256_file(path) if path.is_file() else None


def _shot_key(
    shot: Shot,
    profile: Profile,
    fps: Exact,
    aspect: Aspect,
    trace_sha: str | None,
    scene_sha: str | None,
    scene_tree: str | None,
    style: str | None,
) -> str:
    """Everything that can change a shot's pixels, and nothing about its position in the cut,
    so reordering shots never invalidates a render. Fields that do not reach the renderer
    (intent, claims, checkpoints, narration, how `sim.module` is spelled) are left out: the
    simulation enters only through the trace hash, and `trace_dir` never enters at all."""
    return canonical.sha256_json(
        {
            "renderer": shot.renderer,
            "evidence": shot.evidence.value,  # drawn on screen as the evidence label
            "scene": shot.scene,
            "frames": shot.frames,
            "timemap": None if shot.timemap is None else shot.timemap.model_dump(mode="json"),
            "framing": {"aspect": aspect, **shot.framings[aspect].model_dump(mode="json")},
            "variant_calm": shot.variant_calm,
            "fps": fps,
            "profile": {
                "name": profile.name,
                "width": profile.width,
                "height": profile.height,
                "quality": profile.quality,
            },
            "trace_sha256": trace_sha,
            "scene_sha256": scene_sha,
            "scene_tree_sha256": scene_tree,
            "style_sha256": style,
            "kinetic": __version__,
            "contract": CONTRACT_VERSION,
        }
    )


def compile_plan(loaded: LoadedFilm, ws: Workspace, profile_name: str) -> RenderPlan:
    profile = PROFILES.get(profile_name)
    if profile is None:
        raise PlanError(f"unknown profile {profile_name!r}; expected one of {sorted(PROFILES)}")

    errors = [d for d in validate_film(loaded, ws, run_sims=True) if d.level == "error"]
    if errors:
        lines = "\n".join(f"  {d.where}: {d.message}" for d in errors)
        raise PlanError(f"{loaded.film_path}: {len(errors)} validation error(s)\n{lines}")

    film = loaded.film
    aspect = profile.aspect
    all_shots = film.shots()
    if aspect == "9:16":
        included = [(seq, shot) for seq, shot in all_shots if aspect in shot.framings]
        if not included:
            raise PlanError(f"profile {profile.name!r}: no shot has a {aspect!r} framing")
    else:
        missing = [shot.id for _, shot in all_shots if aspect not in shot.framings]
        if missing:
            raise PlanError(
                f"profile {profile.name!r}: shots without a {aspect!r} framing: "
                + ", ".join(missing)
            )
        included = all_shots

    try:
        refs: dict[str, TraceRef] = dict(run_film_sims(loaded, ws))
    except ValueError as exc:
        raise PlanError(str(exc)) from exc

    style = style_sha(loaded)
    shots: list[ShotPlan] = []
    frame = 0
    for seq, shot in included:
        ref = refs.get(shot.id)
        trace_dir = trace_sha = None
        tick_start = tick_end = None
        if ref is not None and shot.timemap is not None:
            meta = TraceMeta.model_validate_json((ref.directory / META_FILE).read_text("utf-8"))
            trace_dir = _rel(ref.directory, ws.root)
            trace_sha = ref.trace_sha256
            tick_start = seconds_to_ticks(shot.timemap.sim_start_s, meta.tick_rate)
            tick_end = seconds_to_ticks(shot.timemap.sim_end_s, meta.tick_rate)
        scene_path = loaded.film_dir / shot.scene
        scene_sha = canonical.sha256_file(scene_path) if scene_path.is_file() else None
        scene_tree = scene_tree_sha(scene_path, loaded.film_dir, ws.cache_dir)
        key = _shot_key(shot, profile, film.fps, aspect, trace_sha, scene_sha, scene_tree, style)
        shots.append(
            ShotPlan(
                shot_id=shot.id,
                sequence_id=seq.id,
                renderer=shot.renderer,
                scene=shot.scene,
                framing=aspect,
                camera=shot.framings[aspect].camera,
                frame_start=frame,
                frame_count=shot.frames,
                fps=film.fps,
                width=profile.width,
                height=profile.height,
                evidence=shot.evidence,
                trace_dir=trace_dir,
                trace_sha256=trace_sha,
                tick_start=tick_start,
                tick_end=tick_end,
                scene_sha256=scene_sha,
                cache_key=key,
            )
        )
        frame += shot.frames

    film_doc = film.model_dump(mode="json")
    film_doc["claims"] = [c.model_dump(mode="json") for c in loaded.claims]
    return RenderPlan(
        kinetic_version=__version__,
        film_id=film.id,
        profile=profile.name,
        width=profile.width,
        height=profile.height,
        fps=film.fps,
        total_frames=frame,
        shots=shots,
        film_sha256=canonical.sha256_json(film_doc),
    )


def plan_path(ws: Workspace, film_id: str, profile: str) -> Path:
    return ws.cache_dir / "plans" / film_id / profile / PLAN_FILE


def write_plan(plan: RenderPlan, ws: Workspace) -> Path:
    path = plan_path(ws, plan.film_id, plan.profile)
    canonical.write_text_lf(path, canonical.dumps(plan.model_dump(mode="json")) + "\n")
    return path
