# Renderer runtime API (contract v0.1)

This is the contract between the Kinetic core and the code that runs *inside* a renderer
(Godot in M2; Blender and Manim in M4). The core never imports renderer code, and runtimes
never import `ostrium_kinetic`; they exchange only files.

## Files in a shot run directory

| File | Written by | Content |
|---|---|---|
| `job.json` | core | `ShotJob` (`src/ostrium_kinetic/job.py`) |
| `frames/frame%08d.png` | renderer | `frame_count` RGB frames, zero-based, exactly `width`x`height` |
| `annotations.jsonl` | renderer | one `FrameAnnotation` per frame, in frame order |
| `render.log` | adapter | renderer stdout/stderr |
| `shot.json` | adapter | `ShotResult`; written atomically at each status change |
| `mezzanine.mkv` | media | FFV1 RGB mezzanine of the frames |
| `stills/` | QA | checkpoint frames kept after the PNGs are deleted |

## Time

A runtime must compute, for frame `f`:

```
tick = tick_start + ((tick_end - tick_start) * f) // frame_count     # integer arithmetic
```

Never accumulate frame deltas. Godot: `f` is `Engine.get_frames_drawn()`, measured in M0 to
equal the Movie Maker frame index (0-based).

## Sampling the trace (must match `ostrium_kinetic/sim/trace.py`)

- **State at tick t:** fold `trace.jsonl` events with `event.tick <= t`. `entity.created`
  sets state, `state.set` merges, `entity.destroyed` removes the entity.
- **Active spans at tick t:** spans in `spans.jsonl` with `begin_tick <= t < end_tick`.
- **Progress:** `step` gives 0; `linear` gives `(t - begin)/(end - begin)`; `curve:<name>`
  interpolates linearly between `keys` `[u, p]` (strings `"a/b"`).
- Runtimes may not ease the progress of entities that carry claims. They may style
  geometry, trails, materials, cameras and illustrative elements freely.

## Godot runtime (`renderers/godot/addons/kinetic_runtime/`)

The autoload singleton `Kinetic` is processed before scene nodes (`process_priority`
-1000). It updates once per frame and exposes:

| Member | Meaning |
|---|---|
| `job: Dictionary` | parsed `job.json` |
| `frame: int`, `tick: int`, `sim_seconds: float` | current frame, its tick, `tick * tick_rate` |
| `shot_progress: float` | `frame / frame_count` (for illustrative motion only) |
| `calm: bool` | reduced-motion variant requested |
| `state(entity) -> Dictionary` | entity state at `tick`; `{}` if not alive |
| `alive(entity) -> bool` | |
| `active_span(entity) -> Dictionary` | `{span_id, kind, interp, outcome, begin_tick, end_tick, progress, ...attrs}`, or `{}` |
| `spans_active() -> Array` | all active spans, same dictionaries plus `entity` |
| `signals_in(from_tick, to_tick) -> Array` | `signal` events with `from_tick < tick <= to_tick` |
| `track(entity, node: Node3D)` | include the node's screen-space bbox in annotations |
| `label(text, control: Control)` | include an on-screen label's rect in annotations |
| `style(key, default)` | style token from the job |

A shot scene is a `.tscn` under the film's directory. Its root is a `Node3D` that must contain a
`Camera3D` named after every camera in the shot's framings (e.g. `wide`, `tall`). The runner
scene loads `job.json` from the `--kinetic-job <path>` user argument (after `--`), instances
the shot scene, makes `job.camera` current, writes annotations, and quits after
`frame_count` frames. Failures exit non-zero and write `runtime_error.txt` in the run
directory.
