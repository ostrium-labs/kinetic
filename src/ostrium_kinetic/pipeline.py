"""Render -> mezzanine -> assemble -> QA -> provenance for one film and profile.

Each stage reads what the previous one left in the cache, so stages can be re-run alone
(`kinetic render`, `kinetic assemble`, `kinetic qa`) or together (`kinetic build`).
A renderer exit code is never success: a shot only counts once its frames, annotations and
mezzanine have passed QA.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ostrium_kinetic import CONTRACT_VERSION, canonical, log
from ostrium_kinetic.contract.loader import LoadedFilm
from ostrium_kinetic.contract.models import PROFILES, Shot
from ostrium_kinetic.job import (
    ANNOTATIONS_FILE,
    FRAMES_DIR,
    JOB_FILE,
    MEZZANINE_FILE,
    RESULT_FILE,
    STILLS_DIR,
    AdapterError,
    RendererAdapter,
    ShotJob,
    ShotResult,
)
from ostrium_kinetic.plan.compiler import RenderPlan, ShotPlan, compile_plan, write_plan
from ostrium_kinetic.qa.report import QaCheck, QaReport
from ostrium_kinetic.sim.trace import Trace
from ostrium_kinetic.workspace import Workspace

SHOT_QA_FILE = "qa.json"
CONTACT_FILE = "contact.png"


class PipelineError(Exception):
    """A stage could not run (missing renderer, missing earlier stage, unsupported shot)."""


def adapter_for(renderer: str) -> RendererAdapter:
    if renderer == "godot":
        from ostrium_kinetic.adapters.godot import GodotAdapter

        return GodotAdapter()
    raise PipelineError(
        f"renderer {renderer!r} has no adapter yet (Blender and Manim arrive in M4)"
    )


def output_dir(ws: Workspace, film_id: str, profile: str) -> Path:
    return ws.root / "out" / film_id / profile


def _scene_tree_sha(loaded: LoadedFilm, shot: Shot) -> str | None:
    scene_dir = (loaded.film_dir / shot.scene).parent
    return canonical.sha256_tree(scene_dir) if scene_dir.is_dir() else None


def render_key(
    loaded: LoadedFilm,
    shot: Shot,
    plan_shot: ShotPlan,
    adapter: RendererAdapter,
    ws: Workspace,
    *,
    calm: bool,
) -> str:
    """Identity of a shot's run directory: everything that can change its pixels (the plan's
    shot key, the scene directory, the renderer-side runtime and version, the reduced-motion
    variant). QA verdicts are not part of it: annotation checks are re-evaluated on every run
    from the kept annotations.jsonl, so editing a checkpoint or a QA rule never re-renders."""
    return canonical.sha256_json(
        {
            "contract": CONTRACT_VERSION,
            "plan_key": plan_shot.cache_key,
            "scene_tree": _scene_tree_sha(loaded, shot),
            "runtime": adapter.runtime_sha256(),
            "renderer": adapter.name,
            "renderer_version": adapter.version(ws),
            "calm": calm,
        }
    )


def _resolve(ws: Workspace, path: str | None) -> Path | None:
    if path is None:
        return None
    p = Path(path)
    return p if p.is_absolute() else ws.root / p


def build_job(
    loaded: LoadedFilm,
    plan: RenderPlan,
    plan_shot: ShotPlan,
    shot: Shot,
    run_dir: Path,
    ws: Workspace,
    *,
    calm: bool,
) -> ShotJob:
    trace_dir = _resolve(ws, plan_shot.trace_dir)
    tick_rate = None
    if trace_dir is not None:
        tick_rate = Trace.load(trace_dir).meta.tick_rate
    return ShotJob(
        film_id=plan.film_id,
        shot_id=plan_shot.shot_id,
        renderer=plan_shot.renderer,
        film_dir=loaded.film_dir.resolve().as_posix(),
        scene=plan_shot.scene,
        fps=plan_shot.fps,
        width=plan_shot.width,
        height=plan_shot.height,
        frame_count=plan_shot.frame_count,
        framing=plan_shot.framing,
        camera=plan_shot.camera,
        quality=PROFILES[plan.profile].quality,
        trace_dir=trace_dir.resolve().as_posix() if trace_dir else None,
        tick_rate=tick_rate,
        tick_start=plan_shot.tick_start,
        tick_end=plan_shot.tick_end,
        evidence=str(plan_shot.evidence),
        time_label=shot.timemap.label if shot.timemap else None,
        variant_calm=calm,
        out_dir=run_dir.resolve().as_posix(),
    )


def _write_result(run_dir: Path, result: ShotResult) -> None:
    """Atomic: a crash never leaves a half-written shot.json."""
    fd, tmp = tempfile.mkstemp(dir=run_dir, prefix=".shot-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(result.model_dump_json(indent=2) + "\n")
    os.replace(tmp, run_dir / RESULT_FILE)


def read_result(run_dir: Path) -> ShotResult | None:
    path = run_dir / RESULT_FILE
    if not path.is_file():
        return None
    try:
        return ShotResult.model_validate_json(path.read_text("utf-8"))
    except ValueError:
        return None


def _write_checks(run_dir: Path, checks: list[QaCheck]) -> None:
    canonical.write_text_lf(
        run_dir / SHOT_QA_FILE, canonical.dumps([c.model_dump(mode="json") for c in checks]) + "\n"
    )


def read_checks(run_dir: Path) -> list[QaCheck]:
    path = run_dir / SHOT_QA_FILE
    if not path.is_file():
        return []
    return [QaCheck.model_validate(c) for c in json.loads(path.read_text("utf-8"))]


@dataclass
class ShotOutcome:
    plan_shot: ShotPlan
    run_dir: Path
    result: ShotResult
    cached: bool
    checks: list[QaCheck] = field(default_factory=list)

    @property
    def mezzanine(self) -> Path:
        return self.run_dir / MEZZANINE_FILE


@dataclass
class RenderOutcome:
    plan: RenderPlan
    plan_path: Path
    shots: list[ShotOutcome]
    partial: bool = False  # rendered with --shot: not every plan shot is present

    @property
    def ok(self) -> bool:
        return all(
            s.result.status == "mezzanine_ok" and not any(c.status == "fail" for c in s.checks)
            for s in self.shots
        )


Progress = Callable[[str], None]


def _recheck_annotations(
    run_dir: Path, ws: Workspace, plan_shot: ShotPlan, shot: Shot
) -> list[QaCheck]:
    """Re-run annotation QA for a cached shot against today's checkpoints and QA rules; the
    frame and mezzanine checks from render time stand, since the pixels have not changed."""
    from ostrium_kinetic.qa.annotations import check_annotations

    kept = [c for c in read_checks(run_dir) if not c.id.startswith("annotations.")]
    job_path = run_dir / JOB_FILE
    if not job_path.is_file():
        return kept
    job = ShotJob.model_validate_json(job_path.read_text("utf-8"))
    trace_dir = _resolve(ws, plan_shot.trace_dir)
    trace = Trace.load(trace_dir) if trace_dir is not None else None
    fresh = check_annotations(
        job, run_dir / ANNOTATIONS_FILE, trace, shot.checkpoints, plan_shot.shot_id
    )
    checks = kept + fresh
    _write_checks(run_dir, checks)
    return checks


def _shot_spec(loaded: LoadedFilm, shot_id: str) -> Shot:
    for _, shot in loaded.film.shots():
        if shot.id == shot_id:
            return shot
    raise PipelineError(f"shot {shot_id!r} not in film")


def render_film(
    loaded: LoadedFilm,
    ws: Workspace,
    profile: str,
    *,
    shot_id: str | None = None,
    force: bool = False,
    keep_frames: bool = False,
    calm: bool = False,
    collect_only: bool = False,
    progress: Progress = lambda _msg: None,
) -> RenderOutcome:
    """Render every shot of the plan that is not already cached. With `collect_only`, render
    nothing and report what the cache holds (used by `kinetic assemble` and `kinetic qa`)."""
    from ostrium_kinetic.media.ffmpeg import Tools
    from ostrium_kinetic.media.mezzanine import encode_mezzanine
    from ostrium_kinetic.qa.annotations import check_annotations
    from ostrium_kinetic.qa.frames import check_frames
    from ostrium_kinetic.qa.media import check_mezzanine
    from ostrium_kinetic.qa.stills import keep_stills

    plan = compile_plan(loaded, ws, profile)
    plan_file = write_plan(plan, ws)
    tools = None if collect_only else Tools.resolve(ws)
    logger = log.get(component="render", film=plan.film_id, profile=profile)
    targets = [s for s in plan.shots if shot_id is None or s.shot_id == shot_id]
    if shot_id is not None and not targets:
        raise PipelineError(f"shot {shot_id!r} is not in the {profile!r} plan")

    outcomes: list[ShotOutcome] = []
    for plan_shot in targets:
        shot = _shot_spec(loaded, plan_shot.shot_id)
        adapter = adapter_for(plan_shot.renderer)
        use_calm = calm or shot.variant_calm
        key = render_key(loaded, shot, plan_shot, adapter, ws, calm=use_calm)
        run_dir = ws.cache("shots", key[:16])
        previous = read_result(run_dir)
        if (
            not force
            and previous is not None
            and previous.status == "mezzanine_ok"
            and previous.render_key == key
            and (run_dir / MEZZANINE_FILE).is_file()
        ):
            progress(f"{plan_shot.shot_id}: cached ({key[:12]})")
            checks = _recheck_annotations(run_dir, ws, plan_shot, shot)
            outcomes.append(ShotOutcome(plan_shot, run_dir, previous, True, checks))
            continue
        if collect_only:
            missing = ShotResult(
                shot_id=plan_shot.shot_id,
                render_key=key,
                renderer=adapter.name,
                status="failed",
                frame_count=plan_shot.frame_count,
                width=plan_shot.width,
                height=plan_shot.height,
                error="not rendered for the current inputs (run `kinetic render`)",
            )
            outcomes.append(ShotOutcome(plan_shot, run_dir, missing, False))
            continue

        for stale in (FRAMES_DIR, STILLS_DIR):
            shutil.rmtree(run_dir / stale, ignore_errors=True)
        for stale_file in (ANNOTATIONS_FILE, MEZZANINE_FILE, SHOT_QA_FILE, "runtime_error.txt"):
            (run_dir / stale_file).unlink(missing_ok=True)
        job = build_job(loaded, plan, plan_shot, shot, run_dir, ws, calm=use_calm)
        canonical.write_text_lf(run_dir / JOB_FILE, job.model_dump_json(indent=2) + "\n")
        base = ShotResult(
            shot_id=plan_shot.shot_id,
            render_key=key,
            renderer=adapter.name,
            renderer_version=adapter.version(ws),
            status="rendering",
            frame_count=plan_shot.frame_count,
            width=plan_shot.width,
            height=plan_shot.height,
        )
        _write_result(run_dir, base)
        progress(
            f"{plan_shot.shot_id}: rendering {plan_shot.frame_count} frames with {adapter.name}"
        )
        started = time.monotonic()
        try:
            result = adapter.render(job, run_dir, ws)
        except AdapterError as exc:
            failed = base.model_copy(update={"status": "failed", "error": str(exc)})
            _write_result(run_dir, failed)
            logger.error("render_failed", shot=plan_shot.shot_id, error=str(exc))
            outcomes.append(ShotOutcome(plan_shot, run_dir, failed, False))
            continue
        result = result.model_copy(
            update={"render_key": key, "seconds": round(time.monotonic() - started, 3)}
        )

        frames_dir = run_dir / FRAMES_DIR
        trace_dir = _resolve(ws, plan_shot.trace_dir)
        trace = Trace.load(trace_dir) if trace_dir is not None else None
        checks = check_frames(
            frames_dir,
            plan_shot.frame_count,
            plan_shot.width,
            plan_shot.height,
            plan_shot.shot_id,
        )
        checks += check_annotations(
            job, run_dir / ANNOTATIONS_FILE, trace, shot.checkpoints, plan_shot.shot_id
        )
        still_frames = {0, plan_shot.frame_count // 2, plan_shot.frame_count - 1}
        still_frames |= {c.at_frame for c in shot.checkpoints}
        # Only broken frames block the mezzanine. Annotation failures (a wrong checkpoint, a
        # label in the wrong place) are verdicts on good pixels: they are re-checked on later
        # runs without re-rendering, and they still keep the film from assembling.
        frames_ok = not any(c.status == "fail" and c.id.startswith("frames.") for c in checks)
        if frames_ok:
            keep_stills(frames_dir, run_dir / STILLS_DIR, sorted(still_frames))
            assert tools is not None
            encode_mezzanine(
                tools, frames_dir, plan_shot.fps, run_dir / MEZZANINE_FILE, plan_shot.frame_count
            )
            checks += check_mezzanine(
                tools,
                run_dir / MEZZANINE_FILE,
                plan_shot.width,
                plan_shot.height,
                plan_shot.frame_count,
                plan_shot.shot_id,
            )
        _write_checks(run_dir, checks)
        blocking = [c for c in checks if c.status == "fail" and not c.id.startswith("annotations.")]
        if blocking:
            result = result.model_copy(
                update={"status": "failed", "error": "; ".join(c.detail for c in blocking[:3])}
            )
        else:
            result = result.model_copy(update={"status": "mezzanine_ok"})
            if not keep_frames:
                shutil.rmtree(frames_dir, ignore_errors=True)
        _write_result(run_dir, result)
        logger.info("shot_done", shot=plan_shot.shot_id, status=result.status, key=key)
        progress(f"{plan_shot.shot_id}: {result.status} in {result.seconds:.1f}s")
        outcomes.append(ShotOutcome(plan_shot, run_dir, result, False, checks))
    return RenderOutcome(plan, plan_file, outcomes, partial=shot_id is not None)


@dataclass
class AssembleOutcome:
    master: Path
    delivery: Path
    contact_sheets: list[Path]


def assemble_film(outcome: RenderOutcome, ws: Workspace) -> AssembleOutcome:
    """Concatenate every shot's mezzanine (hard cuts, M2) into a lossless master, then encode
    the BT.709-tagged delivery file and a contact sheet per shot."""
    from ostrium_kinetic.media.assemble import concat_mezzanines, encode_delivery
    from ostrium_kinetic.media.ffmpeg import Tools
    from ostrium_kinetic.qa.contact_sheet import make_contact_sheet

    if outcome.partial:
        raise PipelineError("assemble needs every shot of the plan; render without --shot first")
    not_ready = [s.plan_shot.shot_id for s in outcome.shots if s.result.status != "mezzanine_ok"]
    if not_ready:
        raise PipelineError("shots not rendered or failed QA: " + ", ".join(not_ready))
    # The semantic gate: a shot whose annotations contradict the trace or its checkpoints has
    # good pixels but a wrong story, and never becomes part of a deliverable.
    failing = sorted(
        {s.plan_shot.shot_id for s in outcome.shots for c in s.checks if c.status == "fail"}
    )
    if failing:
        raise PipelineError("shots failing QA (fix before assembling): " + ", ".join(failing))
    tools = Tools.resolve(ws)
    plan = outcome.plan
    out = output_dir(ws, plan.film_id, plan.profile)
    out.mkdir(parents=True, exist_ok=True)
    master = concat_mezzanines(tools, [s.mezzanine for s in outcome.shots], out / "master.mkv")
    quality = PROFILES[plan.profile].quality
    name = "social-9x16.mp4" if plan.profile == "social" else "film.mp4"
    delivery = encode_delivery(tools, master, out / name, quality)
    sheets = []
    for s in outcome.shots:
        sheet = s.run_dir / CONTACT_FILE
        make_contact_sheet(tools, s.mezzanine, sheet, s.plan_shot.frame_count)
        target = out / "contact" / f"{s.plan_shot.shot_id}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(sheet, target)
        sheets.append(target)
    return AssembleOutcome(master, delivery, sheets)


def qa_film(outcome: RenderOutcome, assembled: AssembleOutcome | None, ws: Workspace) -> QaReport:
    from ostrium_kinetic.media.ffmpeg import Tools
    from ostrium_kinetic.qa.media import check_delivery, detect_black_and_freeze

    checks: list[QaCheck] = []
    for s in outcome.shots:
        checks += s.checks
        if s.result.status != "mezzanine_ok":
            checks.append(
                QaCheck(
                    id="shot.status",
                    shot_id=s.plan_shot.shot_id,
                    status="fail",
                    detail=f"shot is {s.result.status}: {s.result.error or 'not rendered'}",
                )
            )
    plan = outcome.plan
    delivery = assembled.delivery if assembled is not None else None
    if delivery is None:
        # Standalone `kinetic qa`: re-validate the delivery already on disk, but only if it
        # was assembled from exactly the shots the cache holds now; never drop those checks.
        delivery, current = _existing_delivery(outcome, ws)
        checks.append(current)
    if delivery is not None:
        tools = Tools.resolve(ws)
        checks += check_delivery(
            tools, delivery, plan.width, plan.height, plan.fps, plan.total_frames
        )
        checks += detect_black_and_freeze(tools, delivery)
    report = QaReport(film_id=outcome.plan.film_id, profile=outcome.plan.profile, checks=checks)
    report.write(output_dir(ws, outcome.plan.film_id, outcome.plan.profile) / "qa" / "report.json")
    return report


def _existing_delivery(outcome: RenderOutcome, ws: Workspace) -> tuple[Path | None, QaCheck]:
    """The assembled delivery for this film and profile, if it matches the current shots.

    Its provenance.json records the render key of every shot it was cut from; any difference
    from the current plan means the file on disk shows a film that no longer exists."""
    plan = outcome.plan
    out = output_dir(ws, plan.film_id, plan.profile)
    name = "social-9x16.mp4" if plan.profile == "social" else "film.mp4"
    delivery, prov = out / name, out / "provenance.json"
    if not delivery.is_file() or not prov.is_file():
        return None, QaCheck(
            id="delivery.current",
            shot_id=None,
            status="fail",
            detail=f"no assembled {name} for this profile (run `kinetic assemble`)",
        )
    try:
        recorded = [
            s["render_key"] for s in json.loads(prov.read_text("utf-8"))["deterministic"]["shots"]
        ]
    except (OSError, ValueError, KeyError, TypeError):
        recorded = None
    current = [s.result.render_key for s in outcome.shots]
    if recorded != current:
        return None, QaCheck(
            id="delivery.current",
            shot_id=None,
            status="fail",
            detail=f"{name} was assembled from different shots (run `kinetic assemble`)",
        )
    return delivery, QaCheck(
        id="delivery.current",
        shot_id=None,
        status="pass",
        detail=f"{name} matches the current shots; delivery checks re-run",
    )


def write_film_provenance(
    outcome: RenderOutcome, assembled: AssembleOutcome, ws: Workspace
) -> Path:
    from ostrium_kinetic.env.tools import discover, resolve
    from ostrium_kinetic.provenance import build_provenance, write_provenance

    tools: dict[str, str | None] = {}
    for name in sorted({s.plan_shot.renderer for s in outcome.shots} | {"ffmpeg"}):
        found = resolve(discover(name, ws))
        tools[name] = found.version if found is not None else None
    shots = [
        {
            "shot_id": s.plan_shot.shot_id,
            "render_key": s.result.render_key,
            "trace_sha256": s.plan_shot.trace_sha256,
            "scene_sha256": s.plan_shot.scene_sha256,
            "renderer": s.result.renderer,
            "renderer_version": s.result.renderer_version,
        }
        for s in outcome.shots
    ]
    plan = outcome.plan
    data = build_provenance(
        film_id=plan.film_id,
        film_sha256=plan.film_sha256,
        profile=plan.profile,
        plan_sha256=canonical.sha256_bytes(outcome.plan_path.read_bytes()),
        shots=shots,
        tools=tools,
        outputs=[assembled.master, assembled.delivery, *assembled.contact_sheets],
        workspace_root=ws.root,
        kinetic_root=Path(__file__).resolve().parents[2],
    )
    path = output_dir(ws, plan.film_id, plan.profile) / "provenance.json"
    write_provenance(path, data)
    return path
