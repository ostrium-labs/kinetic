---
name: scene-architect
description: Use when designing how a Kinetic shot is driven - writing or changing a simulation module (sim/*.py), choosing span interpolation, setting a shot's timemap, on-screen time labels and checkpoints in film.yaml, and running kinetic validate/sim/plan. Use after the film-director has a shot's intent, evidence class and claims.
---

# Scene architect (draft, M1)

The simulation decides what happened and when. The renderer decides geometry and look. Your
job is the first half plus the shot fields that bind the two. Contract reference:
`docs/contract.md`. Working example: `examples/smoke-failover/sim/failover.py`.

## 1. Write the simulation module

A simulation is a Python file that defines `TICK_RATE` (seconds per tick, exact) and
`run(ctx)`. It must be pure: no wall clock, no file or network I/O, no rendering imports, no
global mutable state.

```python
from fractions import Fraction

from ostrium_kinetic.sim import Outcome, SimContext

TICK_RATE = Fraction(1, 1000)  # 1 tick = 1 ms


def run(ctx: SimContext) -> None:
    p, world, rec = ctx.params, ctx.world, ctx.rec
    latency = world.ticks(p["latency_s"]) + ctx.rng.int_between(
        "jitter.link", -p["jitter_ms"], p["jitter_ms"]
    )

    rec.entity_type("node", role="str")
    rec.entity_type("link", status="str", latency_ms="int")
    rec.entity_type("packet", status="str")

    rec.create("node:A", role="sender")
    rec.create("link:L", status="up", latency_ms=latency)

    def send() -> None:
        rec.create("packet:1", status="in_flight")
        rec.begin("tx:packet:1", "packet:1", "transit", via="link:L")
        world.after(latency, arrive)

    def arrive() -> None:
        rec.end("tx:packet:1", Outcome.COMPLETED)
        rec.set("packet:1", status="delivered")

    world.at(0, send)
    world.run(until=world.ticks(p["end_s"]))
```

Rules:

- **Time is integer ticks.** Convert exact seconds with `world.ticks("1.2")`; it raises if the
  value is not a whole number of ticks. Pass seconds in params as strings (`"1.2"`), not floats.
- **Declare every entity type** with its fields (`str`, `int`, `float`, `bool`) before creating
  entities. Undeclared types or fields, wrong types (a bool is not an int), NaN and inf are
  errors.
- **Entity ids** are `<type>:<name>` (`node:B`, `packet:7`), created once and never reused,
  even after `destroy`.
- **Events:** `create`, `set` (step semantics), `signal(name, entity, **attrs)` for instants
  (`fault.injected`, `timeout.fired`), `destroy`.
- **Spans:** `begin(span_id, entity, kind, interp=..., **attrs)` and
  `end(span_id, outcome)` with outcome `completed`, `lost` or `aborted`. Every span must be
  closed before the run ends. An entity with an open span cannot be destroyed.
- **Randomness:** only through `ctx.rng`. Use `rng.int_between(stream, low, high)` for jitter;
  integer draws are bit-identical across platforms. Give each independent source of
  randomness its own stream name, so adding one never shifts another.
- **Ordering:** callbacks at the same tick run in scheduling order. You cannot schedule in the
  past.
- **Never tune parameters to hit a published number.** Use the source's specification; report
  disagreements to the film-director as claim discrepancies.
- Put the simulation's simplifications in the docstring and in film `assumptions:`.

## 2. Choose interpolation

| interp | Use for | Progress |
|---|---|---|
| `linear` (default) | transit at constant rate in sim time | `(t - begin) / (end - begin)` |
| `step` | a state that is simply active (holding, waiting); allows zero duration | always 0 |
| `curve:<name>` | a modelled non-linear progress (e.g. slow start) | piecewise-linear over `keys=[(u, p), ...]`, u from 0 to 1, p in [0, 1] |

Renderers may not ease the progress of claim-bearing entities. If motion must not be linear,
model it as a curve in the sim, so every renderer gets the same numbers.

## 3. Bind the shot: timemap, label, framings, checkpoints

```yaml
      - id: S1
        intent: "Packet 1 is lost when P is cut; the resend crosses S and is acknowledged."
        renderer: godot
        scene: scenes/godot/failover.tscn
        evidence: simulated
        claims: [C1]
        frames: 105                       # 3.5 s at 30 fps
        sim:
          module: sim/failover.py         # relative to the film directory
          params:
            latency_p_s: "1.5"
            latency_s_s: "1.2"
            ack_latency_s: "0.6"
            cut_p_at_s: "1.2"
            timeout_s: "1.5"
            reroute_delay_s: "0.1"
            jitter_ms: 20
            end_s: "3.5"
          seed: 7
        timemap: { sim_start_s: 0, sim_end_s: 3.5, label: null }   # 1x: no label needed
        framings:
          "16:9": { camera: wide }
          "9:16": { camera: tall }        # a deliberate reframing, not a crop
        checkpoints:
          - at_frame: 40
            expect:
              - { entity: "link:P", field: status, equals: failed }
          - at_frame: 60
            expect:
              - { entity: "packet:2", field: active_span.via, equals: "link:S" }
```

- Frame `f` shows tick `start + floor((end - start) * f / frames)`. The last frame is just
  before `sim_end_s`, never on it.
- Speed = sim seconds / film seconds. Any speed other than 1 needs a non-empty `label`
  ("time slowed 10x"); `validate` enforces this. Prefer 1x for latency claims.
- `framings` must include `"16:9"`. Cameras are named intents; camera paths live in scene code.
- Checkpoints assert the trace at a frame. `field` is a state field, `active_span.<attr>`
  (`via`, `kind`, `outcome`, ...), or `active_span` (`equals: null` means no active span).
  Put one checkpoint on each beat the shot exists to show, and choose frames a few frames
  away from the event tick so jitter cannot move the beat across the checkpoint.
- On-screen labels show numbers only through claims, with the claim's precision and units.

## 4. Verify

```bash
uv run kinetic validate <film>          # schema, refs, evidence, timemap labels, runs sims, checkpoints
uv run kinetic sim <film> --shot S1     # writes .kinetic/traces/<key>/{trace.jsonl,spans.jsonl,trace.meta.json}
uv run kinetic plan <film> --profile preview
```

- Read the trace (`trace.jsonl`) for the shot and check the story event by event.
- Add property tests for the sim (hypothesis over seeds and parameters) for every invariant
  the shot claims, e.g. "a packet never begins transit on a failed link".
- Run the sim twice; `trace.jsonl` must be byte-identical. `kinetic sim --force` re-runs.
- A green `validate` is necessary, not sufficient: the film-director and the owner still
  review the shot.
