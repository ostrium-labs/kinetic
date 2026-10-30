# Kinetic

**Programmable cinematography for engineering.**

[![ci](https://github.com/ostrium-labs/kinetic/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/ostrium-labs/kinetic/actions/workflows/ci.yml)

Kinetic turns simulations, traces and measurements into reproducible technical films. Every moving thing on screen is either data, or labelled as not data.

> Status: **v0.1 in development, M2 in progress.** The contract, simulation core, plan compiler and environment doctor exist (M1). M2 adds the Godot renderer adapter, lossless FFV1 mezzanines, assembly, QA (`docs/qa.md`) and provenance, proven on `examples/smoke-failover` and the 1080p look-development clip `examples/lookdev-failover`. See `docs/architecture-proposal.md` for the full design and roadmap.

## How it works

1. A **film spec** (`film.yaml`) holds sources, claims, sequences and shots. Every number on screen must cite a claim, and every shot declares its evidence class (`trace`, `measured`, `sourced`, `computed`, `simulated`, `hypothetical`, `illustrative`).
2. A **simulation** (plain Python, seeded, integer ticks) writes a deterministic **trace**: typed events plus resolved spans with explicit interpolation. The simulation decides what happened and when.
3. **Renderers** (Godot, Blender, Manim, from M2 on) sample the trace at integer frame times. A renderer decides how the trace looks; it never invents state.
4. The **plan compiler** gives each shot a content-addressed cache key, so changing one shot re-renders one shot.

## Requirements

- Windows 10/11 or Linux, Python 3.12, [uv](https://docs.astral.sh/uv/) ≥ 0.11
- Optional, for rendering (from M2): Godot 4.7, Blender 5.2 LTS, FFmpeg ≥ 7, Manim Community 0.22

## Quick start

```bash
uv sync
uv run kinetic doctor
uv run kinetic validate examples/smoke-failover
uv run kinetic sim examples/smoke-failover
uv run kinetic plan examples/smoke-failover --profile preview
```

Tool paths that are not on `PATH` go in a local `kinetic.toml` (see `kinetic.toml.example`).

Manim shots (`renderer: manim`) render in Kinetic's own environment: `uv sync --extra manim`
installs Manim Community 0.22.1, then `uv run kinetic build examples/manim-trace-timeline --profile preview`
renders the smoke-failover trace as a timeline. No LaTeX is needed; scenes use Pango text
(`docs/runtime-api.md`, "Manim runtime").

## Commands

| Command | Does |
|---|---|
| `kinetic doctor [--json] [--deep]` | Finds tools, checks versions, shadowing, GPU, encoders, CPU/RAM, disk. `--deep` runs small test renders and encodes. |
| `kinetic validate <film>` | Validates the schema and cross-references, runs the simulations, and evaluates shot checkpoints against the traces |
| `kinetic sim <film> [--shot ID]` | Runs simulations into the trace cache |
| `kinetic plan <film> --profile P` | Compiles `render-plan.json` with per-shot cache keys |
| `kinetic render <film> --profile P [--shot ID] [--force] [--keep-frames] [--calm]` | Renders stale shots (cached by render key) to frames, checks frames and annotations, and encodes a lossless FFV1 mezzanine per shot |
| `kinetic assemble <film> --profile P` | Concatenates the shot mezzanines into `master.mkv`, encodes `film.mp4` (or `social-9x16.mp4`), writes contact sheets, runs QA and writes provenance |
| `kinetic qa <film> --profile P` | Reports per-shot QA from the cache to `out/<film_id>/<profile>/qa/report.json`; exit 1 on any failure |
| `kinetic build <film> --profile P` | Render, assemble, QA and provenance in one command |
| `kinetic schema [--check]` | Regenerates or verifies the committed JSON Schemas |

## License

Apache-2.0. Kinetic invokes but never redistributes third-party tools; see `THIRD_PARTY.md`.
