# Kinetic

Kinetic (`ostrium_kinetic`, CLI `kinetic`) turns deterministic simulations, traces and
measurements into reproducible technical films. A simulation writes a typed, integer-tick
trace; renderers (Godot, Blender, Manim, from M2) sample that trace at integer frame times.
Current milestone: **M1** (contract, sim core, timeline, plan compiler, doctor, CI). No renderer
adapter exists yet, so Kinetic does not produce video.

## Commands

```bash
uv sync                                         # install from uv.lock (Python 3.12)
uv run kinetic doctor [--json] [--deep]         # tool discovery, versions, shadowing, GPU, encoders, disk
uv run kinetic validate examples/smoke-failover # schema, cross-refs, evidence rules, sims, checkpoints
uv run kinetic sim examples/smoke-failover [--shot S1] [--force]   # run sims into the trace cache
uv run kinetic plan examples/smoke-failover --profile preview      # render-plan.json + cache keys
uv run kinetic render examples/smoke-failover --profile preview [--shot S1] [--force] [--keep-frames] [--calm]
                                                # frames -> frame/annotation QA -> FFV1 mezzanine, per shot
uv run kinetic assemble examples/smoke-failover --profile preview  # master.mkv, film.mp4, contact sheets, QA, provenance
uv run kinetic qa examples/smoke-failover --profile preview        # out/<film_id>/<profile>/qa/report.json; 1 on any fail
uv run kinetic build examples/smoke-failover --profile preview     # render + assemble + qa + provenance
uv run kinetic schema [--check]                 # regenerate / verify the committed JSON Schemas
uv run pytest -m "not renderer"                 # unit suites (renderer tests skip without tools)
uv run ruff check && uv run ruff format --check
uv run pyright
```

Exit codes: 0 success, 1 a check or validation failed, 2 usage or runtime error.
Generated files go to `.kinetic/` (cache, traces, plans, logs, `shots/<key16>/` run directories)
and `out/<film_id>/<profile>/`; both are git-ignored. A zero exit from `render` or `build` is not
success: read `qa/report.json` and view the stills and contact sheets (`render-and-verify` skill).

## Hard rules

- Never hand-edit `src/ostrium_kinetic/schemas/`. Change the pydantic models, then run
  `uv run kinetic schema`. CI runs `kinetic schema --check`.
- A renderer exit code is never success. From M2, QA (structural probes, semantic
  checkpoints, viewed contact sheets) decides whether a shot passed.
- Every number on screen cites a claim id in the claims ledger. Shots and claims carry an
  evidence class (`docs/evidence-policy.md`).
- A simulation is never tuned to reproduce a published number. If the sim and the source
  disagree, record the discrepancy in the claim; the disagreement is the finding.
- Renderers never invent state. They read `state_at(tick)` and resolved spans from the
  trace. Motion not derived from the trace is `illustrative` and carries no quantities.
- Simulations are pure: integer ticks, seeded `RngStreams`, no wall clock, no float time, no
  rendering imports. Same inputs must give byte-identical `trace.jsonl`.
- Blender runtime code (`renderers/blender/`, M4) is GPL-2.0-or-later and never imports
  `ostrium_kinetic`; it talks to the core only through job/trace JSON files.
- No AI-attribution lines in commits, PR bodies or files.
- `tests/golden/smoke-failover.trace.sha256` is the cross-platform determinism gate. If it
  changes, the trace contract changed; treat that as a breaking change, not a test update.

## Source map (`src/ostrium_kinetic/`)

| Module | Role |
|---|---|
| `canonical.py` | Exact rationals (`to_fraction`, `Exact`), canonical JSON, CRLF-safe file/tree hashes |
| `timeline.py` | Fraction fps, frame <-> seconds, seconds -> ticks, `ShotClock` (frame -> sim tick) |
| `workspace.py` | Finds the workspace root and `kinetic.toml`; cache directory |
| `log.py` | structlog JSON lines to `<cache>/logs/` |
| `cli/main.py` | typer app: `doctor`, `validate`, `sim`, `plan`, `schema`; thin wrappers only |
| `contract/models.py` | pydantic v2 film contract (film, shot, claim, evidence, timemap, checkpoint, profile) |
| `contract/loader.py` | Reads `film.yaml` (+ claims file) into models with readable errors |
| `contract/validate.py` | Cross-refs, evidence rules, timemap labels, runs sims, evaluates checkpoints |
| `sim/model.py` | Trace contract v0.1 records: `TraceEvent`, `Span`, `TraceMeta`, entity id pattern |
| `sim/rng.py` | `RngStreams`: named, independent PCG64 streams from one seed |
| `sim/world.py` | `World` integer-tick event loop, `Recorder` (entity types, events, spans), `SimContext` |
| `sim/trace.py` | `Trace.load` (sha-checked), `state_at`, `spans_active`, span progress (reference sampler) |
| `sim/runner.py` | Loads a sim module, runs it, content-addressed trace cache (`ensure_trace`) |
| `plan/simulate.py` | Runs a film's simulations into the trace cache |
| `plan/compiler.py` | Compiles `render-plan.json` with a cache key per shot |
| `schema_export.py` | Generates the JSON Schemas in `schemas/` from the models |
| `env/` | `kinetic doctor`: tool discovery, checks, hardware facts, `--deep` probes |

Other places: `examples/smoke-failover/` (film.yaml + `sim/failover.py`), `tests/unit/`,
`tests/golden/`, `docs/` (contract, evidence policy, Windows setup, ADRs), `.claude/skills/`.

## Windows notes

- Use the winget uv (0.13.x) and run everything through `uv run`. If `uv --version` prints
  0.11.x, the pip-installed copy in `Python312\Scripts` is shadowing it: move the winget uv
  PATH entry above it (`docs/windows-setup.md`). Open a fresh terminal after
  `uv tool update-shell` or PATH edits.
- Godot: use `Godot_v4.7.2-stable_win64_console.exe` (stdout is captured). Never pass
  `--headless`; Movie Maker needs a real window and GPU context.
- Godot output size comes from the per-run project viewport (`viewport_width/height` +
  stretch mode `viewport`), not from `--resolution` or the window size.
- Tool paths that are not on PATH go in `kinetic.toml` `[tools]` (copy
  `kinetic.toml.example`); `KINETIC_<TOOL>` environment variables override it.
- Always pass subprocess arguments as lists; never `shell=True`.

## Where the plan lives

- `docs/architecture-proposal.md`: full design, trace contract v0.1 (section 7), milestones
  (section 14), M0 results (end of file).
- `docs/adr/`: decisions 0001-0005.
- `docs/contract.md`, `docs/evidence-policy.md`, `docs/windows-setup.md`.
- Skills: `.claude/skills/film-director/` (sources -> claims -> shot list) and
  `.claude/skills/scene-architect/` (simulation, timemap, checkpoints).
