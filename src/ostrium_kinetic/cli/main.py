"""The `kinetic` command line. Commands are thin: each delegates to one library function.

Exit codes: 0 success, 1 a check or validation failed, 2 usage or runtime error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from ostrium_kinetic import __version__, log
from ostrium_kinetic.workspace import Workspace

app = typer.Typer(
    name="kinetic",
    help="Programmable cinematography for engineering.",
    no_args_is_help=True,
    add_completion=False,
)

FilmArg = Annotated[
    Path, typer.Argument(help="Film directory (containing film.yaml) or a film YAML file.")
]


def _workspace(start: Path | None = None) -> Workspace:
    ws = Workspace.find(start)
    log.configure(ws)
    return ws


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
    except (FilmLoadError, KeyError, ValueError) as exc:
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


if __name__ == "__main__":
    app()
