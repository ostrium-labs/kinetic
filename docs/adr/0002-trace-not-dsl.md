# ADR 0002: The shared abstraction is a trace, not a scene DSL

**Status:** Accepted (M1, 2026-10-30)

## Context

Three renderers with different strengths (Godot for trace-driven mechanism shots, Manim for
arithmetic and charts, Blender for hero shots) must show the same mechanism consistently. A
universal scene description would have to express geometry, cameras and materials for all
three, and it still could not express real simulations.

## Decision

- A simulation writes a deterministic, typed trace (`trace.jsonl`, `spans.jsonl`,
  `trace.meta.json`; contract v0.1 in `docs/contract.md`). Every renderer samples it at
  integer frame times through `state_at(tick)` and resolved spans.
- Renderers share the trace, the timeline, style tokens, evidence labels and the output
  profile. They do not share scene geometry or camera descriptions. Scenes are
  renderer-native code behind one job interface.
- `film.yaml` is declarative: intent, timing, data references and assertions. It never holds
  geometry or camera curves. A new top-level contract field needs an ADR.

## Consequences

- "Same trace, same checkpoints" is the test of renderer independence. In M4, Godot and
  Blender variants of the smoke shot must pass the same checkpoints.
- `ostrium_kinetic.sim.trace` is the reference sampler. GDScript, bpy and Manim runtimes must
  match it, including fold and span-boundary semantics.
- Scene code is written per renderer. Reuse comes from primitives promoted after two films,
  not from a DSL.
- Rejected: a hand-written JSON Schema as the master (duplicates the types), pure-Python scene
  files (not diffable data, hard to hash), a custom DSL (cannot express simulations).
