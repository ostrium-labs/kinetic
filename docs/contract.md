# Kinetic contract v0.1

This is the reference for `film.yaml` and the trace files. The pydantic models in
`ostrium_kinetic.contract.models` and `ostrium_kinetic.sim.model` are the source of truth; the
JSON Schemas in `src/ostrium_kinetic/schemas/` are generated from them (`kinetic schema`) and
never edited by hand. Design rationale: `docs/architecture-proposal.md` section 7 and ADRs
0002 and 0004.

## Exact numbers

Fields typed *Exact* accept an integer, a decimal (`1.2`), or an `"a/b"` string, and are read
as exact rationals. A YAML float is converted through its shortest decimal form, so `1.2`
means exactly 6/5. They serialise as `"a/b"` (or `"a"`) strings. Booleans are rejected.

## film.yaml

```yaml
kinetic: "0.1"                    # contract version; required
id: kinetic.smoke-failover
title: "Failover in three and a half seconds"
fps: 30                           # Exact, > 0; default 30 (30000/1001 allowed)
sources:                          # each needs url or path
  - { id: post, url: "https://example.org/post", retrieved: 2026-10-30, sha256: "..." }
claims:                           # inline list, or a path to a YAML list (or {claims: [...]})
  - { id: C1, text: "...", evidence: simulated }
assumptions:
  - { id: A1, text: "Latencies are illustrative values, not measurements." }
style: ../style/house.yaml        # optional
sequences:
  - id: Q1
    title: "Failover"
    shots:
      - id: S1
        intent: "What the shot must make the viewer understand."
        renderer: godot           # godot | blender | manim
        scene: scenes/godot/failover.tscn   # relative to the film directory
        evidence: simulated       # see docs/evidence-policy.md
        claims: [C1]
        frames: 105               # shot length in frames at film fps, > 0
        sim: { module: sim/failover.py, params: { end_s: "3.5" }, seed: 7 }
        timemap: { mode: linear, sim_start_s: 0, sim_end_s: 3.5, label: null }
        framings:
          "16:9": { camera: wide }  # required
          "9:16": { camera: tall }  # optional; a reframing, not a crop
        checkpoints:
          - at_frame: 40
            expect:
              - { entity: "link:P", field: status, equals: failed }
        narration: N1             # optional NarrationSegment id
        variant_calm: false       # reduced-motion variant supported by the scene
narration:
  - { id: N1, text: "..." }
```

Unknown keys are errors everywhere (`extra = forbid`).

### Claims and sources

| Field | Notes |
|---|---|
| `claims[].id`, `text`, `evidence` | required |
| `claims[].source` | a `sources[].id`; required for `sourced`, `measured`, `trace` |
| `claims[].anchor` | where in the source (heading, line) |
| `claims[].notes` | discrepancy notes, derivations |

### Shot rules enforced by `kinetic validate`

- Ids are unique across sequences and shots, claims, sources, narration and assumptions.
- Every referenced claim, source and narration id exists.
- `simulated` shots need `sim`; `illustrative` shots must not have `sim`, and get a warning
  when they cite a quantitative claim (`measured`, `sourced`, `computed`, `trace`).
- `trace` evidence (recorded traces) is not supported until a later milestone.
- A shot with `sim` needs a `timemap`. `sim_end_s > sim_start_s >= 0`, and both must be whole
  numbers of the simulation's ticks.
- If the timemap speed is not 1 (sim seconds / film seconds), `label` must be non-empty.
- The sim module must exist; a missing scene file is a warning in M1.
- When `style` is set, the style file must exist.
- A simulation that calls `sys.exit()` (or raises another non-`Exception` error) fails its
  shot like any other simulation error.
- Checkpoints must lie inside the shot (`at_frame < frames`) and must hold against the trace.

### Timemap

Linear only in v0.1. Frame `f` (0 <= f < frames) shows sim tick

    start + floor((end - start) * f / frames)

with `start = sim_start_s / tick_rate` and `end = sim_end_s / tick_rate`. Frame 0 shows
`start`; the last frame shows a tick strictly before `end`.

### Checkpoints

`expect` is a list of `{entity, field, equals}`. `field` is:

- a state field (`status`): compared with `state_at(tick)[entity][field]`;
- `active_span.<attr>`: an attribute of the entity's active span (`via`, `from`, `to`, or
  `span_id`, `kind`, `interp`, `begin_tick`, `end_tick`, `outcome`);
- `active_span`: the active span's id, or `equals: null` for "no active span".

Booleans only match booleans. An absent entity, field or span never matches.

## Trace files

`kinetic sim` writes three files to `.kinetic/traces/<key[:16]>/`. The key is the sha256 of
the canonical JSON of the contract version, the kinetic version, the simulation source hash
(every `.py` file under the module's directory, line endings normalised, ordered by relative
POSIX path as a plain string), the entry module's file name, params and seed. A cache hit is
used only when its `trace.meta.json` records the same module file name, source hash, params
and seed; otherwise it is rebuilt.

### trace.jsonl

One canonical JSON object per line (sorted keys, no whitespace, UTF-8, LF), in `(tick, seq)`
order. Fields that do not apply are omitted.

| Field | Type | Present on |
|---|---|---|
| `tick` | int >= 0 | all |
| `seq` | int >= 0, emission counter, unique | all |
| `type` | event type | all |
| `entity` | entity id | all |
| `entity_type` | str | `entity.created` |
| `state` | {field: scalar} | `entity.created`, `state.set` |
| `name` | str, e.g. `fault.injected`, `timeout.fired` | `signal` |
| `span_id` | str | `span.begin`, `span.end` |
| `kind` | str, e.g. `transit` | `span.begin` |
| `interp` | `step` / `linear` / `curve:<name>` | `span.begin` |
| `keys` | [[u, p], ...] Exact | `span.begin` with a curve |
| `outcome` | `completed` / `lost` / `aborted` | `span.end` |
| `attrs` | {name: scalar} | `signal`, `span.begin` (when non-empty) |

Event types:

| Type | Meaning |
|---|---|
| `entity.created` | entity appears with its initial state |
| `state.set` | some fields change; step semantics, holds until the next change |
| `signal` | instantaneous named occurrence |
| `span.begin` / `span.end` | interval boundaries; resolved into `spans.jsonl` |
| `entity.destroyed` | entity disappears; not allowed while it has an open span |

Scalars are `str`, `int`, `float`, `bool` or `null`. NaN and infinity are rejected. Time is
never a float.

### Entity ids and types

Ids are `<type>:<name>`: type `^[a-z][a-z0-9_-]*`, name `[A-Za-z0-9_.-]+` (e.g. `node:B`,
`packet:7`). An id is created once and never reused in a trace, even after it is destroyed.
Each type is declared with its fields and their types (`str`, `int`, `float`, `bool`);
`float` fields accept ints, and `bool` is never accepted as `int` or `float`.

### spans.jsonl

One resolved span per line, ordered by `(begin_tick, end_tick, span_id)`:

```json
{"attrs":{"from":"node:A","to":"node:B","via":"link:P"},"begin_tick":0,"end_tick":1200,"entity":"packet:1","interp":"linear","kind":"transit","outcome":"lost","span_id":"tx:packet:1"}
```

A span is active on `[begin_tick, end_tick)`: active on its begin tick, inactive on its end
tick. Progress while active:

| interp | progress at tick t, with u = (t - begin) / (end - begin) |
|---|---|
| `step` | 0 (the span is simply active) |
| `linear` | u |
| `curve:<name>` | piecewise-linear over `keys` [(u, p)]; keys start at u=0, end at u=1, strictly increasing u, p in [0, 1] |

Zero-duration spans are allowed only with `step`. Every span must be closed when the
simulation ends. Renderers may not ease the progress of claim-bearing entities.

### State semantics

`state_at(tick)` folds every event with `event.tick <= tick` in `(tick, seq)` order, so it is
inclusive of the tick: an entity created at tick 10 exists at tick 10, a value set at tick 10
is visible at tick 10, and an entity destroyed at tick 20 is absent at tick 20.
`ostrium_kinetic.sim.trace.Trace` is the reference implementation that renderer runtimes
must match.

### trace.meta.json

| Field | Meaning |
|---|---|
| `contract` | `"0.1"` |
| `kinetic_version` | package version that wrote the trace |
| `tick_rate` | seconds per tick, Exact (e.g. `"1/1000"`) |
| `end_tick` | the world clock when the run ended |
| `seed`, `params` | the simulation inputs |
| `sim` | `{module, source_sha256}` |
| `entity_types` | `{type: {field: type}}` |
| `event_count`, `span_count` | line counts of the two JSONL files |
| `files` | `{"trace.jsonl": sha256, "spans.jsonl": sha256}` |

`trace.meta.sha256` (the sha256 of `trace.meta.json`, one line) is required next to it.
`Trace.load` checks it, requires `files` to list exactly the two JSONL files, re-hashes both
and refuses a trace that fails any check; the cache then rebuilds the entry (as it does for
any error reading a cached entry).

### Render-plan cache keys

A shot's `cache_key` hashes only what reaches the renderer: renderer, evidence, scene path,
frames, timemap, the framing for the profile's aspect, `variant_calm`, film fps, the profile
(name, size, quality), the trace sha256, the scene file's sha256, the hash of the scene's
directory tree (only the scene file when it sits directly in the film directory), the style
file's sha256 when `style` is set, and the contract and kinetic versions. Intent, claims,
checkpoints, narration, the spelling of `sim.module` and the shot's position in the cut do
not change it, so two shots with identical render inputs share a key. `trace_dir` is never
part of a key; it is relative to the workspace root, or absolute (machine-specific) when the
cache lives outside the workspace.

## Determinism

Same simulation source, params and seed give byte-identical `trace.jsonl` and `spans.jsonl`
on every platform. This is enforced by:

- integer ticks and exact rationals, never float time;
- `(tick, seq)` ordering, FIFO for callbacks scheduled at the same tick;
- named PCG64 streams from `numpy.random.SeedSequence(seed, spawn_key=(sha256(name)[:8],))`,
  with integer draws (`int_between`) for jitter;
- canonical JSON with LF line endings.

The golden hashes of the smoke-failover trace and spans (seed 7) are in
`tests/golden/smoke-failover.trace.sha256` and `tests/golden/smoke-failover.spans.sha256` and
are checked on Windows and Ubuntu CI. Plans and
annotations are byte-identical too. Rendered pixels are only perceptually identical, and
pixel goldens are compared on the same machine class only.
