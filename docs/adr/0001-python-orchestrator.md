# ADR 0001: Python 3.12 orchestrator managed with uv

**Status:** Accepted (M1, 2026-10-30)

## Context

Kinetic orchestrates three renderers (Godot, Blender, Manim) and FFmpeg as subprocesses, and
owns the film contract, the simulation core, the timeline, media assembly, QA and provenance.
Blender (`bpy`) and Manim are Python-native. The organisation's main language is Rust; the
first consumer is a website. The development machine is a Windows 11 laptop (i5-1135G7,
15.7 GB RAM, Intel Iris Xe).

## Decision

- Kinetic is a Python 3.12 package (`ostrium-kinetic`, import `ostrium_kinetic`) with a typer
  CLI `kinetic`, managed with uv. `uv.lock` is committed; `.python-version` pins 3.12.
- Contract models are pydantic v2; YAML is read with ruamel.yaml; logs are structlog JSON
  lines.
- Renderers and FFmpeg always run as separate processes, called with `list[str]` arguments,
  never `shell=True`.
- No SimPy: the simulation core is a small integer-tick event loop that is easier to make
  byte-reproducible.

## Consequences

- One language for the core, the Manim adapter and Manim scenes. Blender scripts are Python
  too, but live in a separate GPL-licensed runtime that never imports `ostrium_kinetic`.
- The orchestration work is I/O and subprocess bound, so Python speed is not a constraint.
- Rust was rejected: it adds a compile loop and still needs Python for `bpy` and Manim.
  TypeScript was rejected: a website consumer is not a reason to choose it.
- M0 found that the `uv` on PATH was a shadowed pip copy (0.11.31) ahead of the winget 0.13.6.
  The fix is PATH order (`docs/windows-setup.md`). CI installs uv with `astral-sh/setup-uv`.
