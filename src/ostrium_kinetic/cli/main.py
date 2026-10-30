"""The `kinetic` command line. Commands are thin: each delegates to one library function.

Exit codes: 0 success, 1 a check or validation failed, 2 usage or runtime error.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

import typer

from ostrium_kinetic import __version__, log
from ostrium_kinetic.workspace import Workspace

if TYPE_CHECKING:
    from ostrium_kinetic.contract.loader import LoadedFilm
    from ostrium_kinetic.pipeline import RenderOutcome

app = typer.Typer(
    name="kinetic",
    help="Programmable cinematography for engineering.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)

FilmArg = Annotated[
    Path, typer.Argument(help="Film directory (containing film.yaml) or a film YAML file.")
]


def _workspace(start: Path | None = None) -> Workspace:
    try:
        ws = Workspace.find(start)
        ws.cache_dir  # noqa: B018 - validates the [cache] table early
    except Exception as exc:  # malformed kinetic.toml is a usage error, not a failed check
        raise _fail(f"kinetic.toml: {exc}", 2) from exc
    log.configure(ws)
    return ws


def _check_profile(profile: str) -> None:
    from ostrium_kinetic.contract.models import PROFILES

    if profile not in PROFILES:
        raise _fail(f"unknown profile {profile!r}; expected one of {sorted(PROFILES)}", 2)


def _fail(message: str, code: int = 2) -> typer.Exit:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    return typer.Exit(code)


@app.callback(invoke_without_command=True)
def _root(
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    if version:
        typer.echo(f"kinetic {__version__}")
        raise typer.Exit(0)


@app.command()
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
    deep: Annotated[
        bool, typer.Option("--deep", help="Also run small test renders and encodes.")
    ] = False,
) -> None:
    """Check tools, versions, PATH shadowing, GPU, encoders, CPU/RAM and disk."""
    from ostrium_kinetic.env.doctor import run_doctor

    ws = _workspace()
    report = run_doctor(ws, deep=deep)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        typer.echo(report.render_text())
    raise typer.Exit(report.exit_code)


@app.command()
def validate(
    film: FilmArg,
    no_sim: Annotated[
        bool, typer.Option("--no-sim", help="Skip running simulations and checkpoints.")
    ] = False,
) -> None:
    """Validate a film: schema, cross-references, evidence rules, and shot checkpoints."""
    from ostrium_kinetic.contract.loader import FilmLoadError, load_film
    from ostrium_kinetic.contract.validate import validate_film

    ws = _workspace(film)
    try:
        loaded = load_film(film)
    except FilmLoadError as exc:
        raise _fail(str(exc), 1) from exc
    diagnostics = validate_film(loaded, ws, run_sims=not no_sim)
    errors = 0
    for d in diagnostics:
        color = typer.colors.RED if d.level == "error" else typer.colors.YELLOW
        typer.secho(f"{d.level}: {d.where}: {d.message}", fg=color)
        errors += d.level == "error"
    if errors:
        raise _fail(f"{errors} error(s) in {loaded.film_path}", 1)
    typer.secho(f"ok: {loaded.film.id} ({len(diagnostics)} warning(s))", fg=typer.colors.GREEN)


@app.command()
def sim(
    film: FilmArg,
    shot: Annotated[str | None, typer.Option(help="Only the simulation for this shot id.")] = None,
    force: Annotated[bool, typer.Option(help="Re-run even when a cached trace exists.")] = False,
) -> None:
    """Run the film's simulations into the trace cache."""
    from ostrium_kinetic.contract.loader import FilmLoadError, load_film
    from ostrium_kinetic.plan.simulate import run_film_sims

    ws = _workspace(film)
    try:
        loaded = load_film(film)
        results = run_film_sims(loaded, ws, shot_id=shot, force=force)
    except KeyError as exc:  # unknown --shot: usage error
        raise _fail(str(exc.args[0]) if exc.args else str(exc), 2) from exc
    except (FilmLoadError, ValueError) as exc:
        raise _fail(str(exc), 1) from exc
    for shot_id, ref in results:
        state = "cached" if ref.cached else "ran"
        typer.echo(f"{shot_id}: {state} trace {ref.trace_sha256[:12]} -> {ref.directory}")


@app.command()
def plan(
    film: FilmArg,
    profile: Annotated[
        str, typer.Option(help="Output profile: preview, production, social, archival.")
    ] = "preview",
) -> None:
    """Compile render-plan.json with a content-addressed cache key per shot."""
    from ostrium_kinetic.contract.loader import FilmLoadError, load_film
    from ostrium_kinetic.plan.compiler import PlanError, compile_plan, write_plan

    _check_profile(profile)
    ws = _workspace(film)
    try:
        loaded = load_film(film)
        render_plan = compile_plan(loaded, ws, profile)
    except (FilmLoadError, PlanError) as exc:
        raise _fail(str(exc), 1) from exc
    path = write_plan(render_plan, ws)
    for shot in render_plan.shots:
        typer.echo(
            f"{shot.shot_id}: {shot.renderer} {shot.frame_count}f @ {shot.width}x{shot.height} "
            f"key {shot.cache_key[:12]}"
        )
    typer.echo(f"plan -> {path}")


ProfileOpt = Annotated[
    str, typer.Option(help="Output profile: preview, production, social, archival.")
]


def _load(film: Path, profile: str) -> tuple[Workspace, LoadedFilm]:
    from ostrium_kinetic.contract.loader import FilmLoadError, load_film

    _check_profile(profile)
    ws = _workspace(film)
    try:
        return ws, load_film(film)
    except FilmLoadError as exc:
        raise _fail(str(exc), 1) from exc


def _pipeline_errors() -> tuple[type[Exception], ...]:
    from ostrium_kinetic.media.ffmpeg import MediaError
    from ostrium_kinetic.pipeline import PipelineError
    from ostrium_kinetic.plan.compiler import PlanError

    return (PipelineError, PlanError, MediaError)


def _print_shots(outcome: RenderOutcome) -> None:
    for s in outcome.shots:
        state = "cached" if s.cached else s.result.status
        fails = [c for c in s.checks if c.status == "fail"]
        line = f"{s.plan_shot.shot_id}: {state}  {s.run_dir}"
        color = typer.colors.RED if s.result.status == "failed" else None
        typer.secho(line, fg=color)
        for c in fails[:5]:
            typer.secho(f"    fail {c.id}: {c.detail}", fg=typer.colors.RED)
        if s.result.status == "failed" and s.result.error and not fails:
            typer.secho(f"    {s.result.error.splitlines()[-1]}", fg=typer.colors.RED)


@app.command()
def render(
    film: FilmArg,
    profile: ProfileOpt = "preview",
    shot: Annotated[str | None, typer.Option(help="Only this shot id.")] = None,
    force: Annotated[bool, typer.Option(help="Re-render even when cached.")] = False,
    keep_frames: Annotated[
        bool, typer.Option("--keep-frames", help="Keep PNG frames after the mezzanine.")
    ] = False,
    calm: Annotated[bool, typer.Option(help="Render the reduced-motion variant.")] = False,
) -> None:
    """Render shots to verified frames and lossless mezzanines (cached by render key)."""
    from ostrium_kinetic.pipeline import render_film

    ws, loaded = _load(film, profile)
    try:
        outcome = render_film(
            loaded,
            ws,
            profile,
            shot_id=shot,
            force=force,
            keep_frames=keep_frames,
            calm=calm,
            progress=typer.echo,
        )
    except _pipeline_errors() as exc:
        raise _fail(str(exc), 1) from exc
    _print_shots(outcome)
    if not outcome.ok:
        raise _fail("one or more shots failed", 1)


def _assemble_and_qa(film: Path, profile: str, *, render_first: bool, calm: bool) -> None:
    from ostrium_kinetic.pipeline import (
        assemble_film,
        qa_film,
        render_film,
        write_film_provenance,
    )

    ws, loaded = _load(film, profile)
    try:
        outcome = render_film(
            loaded,
            ws,
            profile,
            calm=calm,
            collect_only=not render_first,
            progress=typer.echo,
        )
        assembled = assemble_film(outcome, ws) if outcome.ok else None
        if assembled is None:
            failing = sorted(
                {
                    s.plan_shot.shot_id
                    for s in outcome.shots
                    if s.result.status != "mezzanine_ok"
                    or any(c.status == "fail" for c in s.checks)
                }
            )
            typer.secho(
                "not assembled: shots failing QA (fix before assembling): " + ", ".join(failing),
                fg=typer.colors.RED,
                err=True,
            )
        report = qa_film(outcome, assembled, ws)
        prov = write_film_provenance(outcome, assembled, ws) if assembled else None
    except _pipeline_errors() as exc:
        raise _fail(str(exc), 1) from exc
    _print_shots(outcome)
    if assembled is not None:
        typer.echo(f"master   -> {assembled.master}")
        typer.echo(f"delivery -> {assembled.delivery}")
        typer.echo(f"contact  -> {assembled.contact_sheets[0].parent}")
    if prov is not None:
        typer.echo(f"provenance -> {prov}")
    typer.echo(report.render_text())
    raise typer.Exit(report.exit_code)


@app.command()
def build(
    film: FilmArg,
    profile: ProfileOpt = "preview",
    calm: Annotated[bool, typer.Option(help="Render the reduced-motion variant.")] = False,
) -> None:
    """Render, assemble, QA and write provenance in one go."""
    _assemble_and_qa(film, profile, render_first=True, calm=calm)


@app.command()
def assemble(
    film: FilmArg,
    profile: ProfileOpt = "preview",
    calm: Annotated[bool, typer.Option(help="Assemble the reduced-motion variant.")] = False,
) -> None:
    """Assemble already-rendered shots into master and delivery files, then run QA."""
    _assemble_and_qa(film, profile, render_first=False, calm=calm)


@app.command()
def qa(
    film: FilmArg,
    profile: ProfileOpt = "preview",
    calm: Annotated[bool, typer.Option(help="Check the reduced-motion variant.")] = False,
) -> None:
    """Report QA for what is in the cache (no rendering, no assembly)."""
    from ostrium_kinetic.pipeline import output_dir, qa_film, render_film

    ws, loaded = _load(film, profile)
    try:
        outcome = render_film(
            loaded,
            ws,
            profile,
            calm=calm,
            collect_only=True,
        )
        report = qa_film(outcome, None, ws)
    except _pipeline_errors() as exc:
        raise _fail(str(exc), 1) from exc
    typer.echo(report.render_text())
    typer.echo(f"report -> {output_dir(ws, outcome.plan.film_id, profile) / 'qa' / 'report.json'}")
    raise typer.Exit(report.exit_code)


@app.command()
def schema(
    check: Annotated[
        bool, typer.Option(help="Fail if committed schemas differ from the models.")
    ] = False,
) -> None:
    """Regenerate (or verify) the JSON Schemas generated from the contract models."""
    from ostrium_kinetic.schema_export import SCHEMA_DIR, export_schemas

    stale = []
    for name, text in export_schemas().items():
        path = SCHEMA_DIR / name
        current = path.read_text("utf-8") if path.exists() else None
        if current != text:
            stale.append(name)
            if not check:
                path.write_text(text, encoding="utf-8", newline="\n")
    if check and stale:
        raise _fail("schemas out of date: " + ", ".join(stale) + " (run `kinetic schema`)", 1)
    typer.echo("schemas up to date" if not stale else "updated: " + ", ".join(stale))


def main() -> None:
    """Console entry point: unexpected errors exit 2 with one line, details go to the log."""
    try:
        app()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        typer.secho("interrupted", fg=typer.colors.RED, err=True)
        sys.exit(130)
    except Exception as exc:
        log.get(component="cli").exception("internal_error")
        typer.secho(f"internal error: {type(exc).__name__}: {exc}", fg=typer.colors.RED, err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
