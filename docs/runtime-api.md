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
- **Planned progress:** the same, with `planned_end_tick` in place of `end` when the span has
  one (otherwise equal to progress). A span cut short (`lost`, `aborted`) reaches progress 1
  at the cut, so **a runtime or scene must use planned progress to place an entity on an
  interrupted path**, and the stopped position (planned progress at `end_tick`) after the span
  has ended. See "Planned end" in `docs/contract.md`.
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
| `evidence: String`, `time_label: String` | `job.evidence` / `job.time_label`, or `""` when absent |
| `state(entity) -> Dictionary` | entity state at `tick`; `{}` if not alive |
| `alive(entity) -> bool` | |
| `active_span(entity) -> Dictionary` | `{span_id, kind, interp, outcome, begin_tick, end_tick, progress, planned_progress, ...attrs}` plus `planned_end_tick` when the span has one, or `{}`; span fields win over attrs of the same name. Place entities by `planned_progress` |
| `last_span(entity) -> Dictionary` | the entity's most recent span with `end_tick <= tick`, or `{}`: progress 1.0 and `planned_progress` = where it stopped (short of 1.0 for a span cut before its planned end; Python `stopped_progress`); Python twin `Trace.last_span_of`. Use it to show where an entity ended up (e.g. a delivered packet docked at its destination) without remembering state across frames |
| `spans_active() -> Array` | all active spans, same dictionaries plus `entity` |
| `signals_in(from_tick, to_tick) -> Array` | `signal` events with `from_tick < tick <= to_tick` |
| `track(entity, node: Node3D)` | include the node's screen-space bbox in annotations |
| `label(text, control: Control)` | include an on-screen label's rect in annotations |
| `style(key, default)` | style token from the job |
| `job.evidence`, `job.time_label` | the shot's evidence class (draw its badge, `docs/evidence-policy.md`) and the time-scale label to show when sim speed is not 1; never hard-code these in a scene |

A shot scene is a `.tscn` under the film's directory. Its root is a `Node3D` that must contain a
`Camera3D` named after every camera in the shot's framings (e.g. `wide`, `tall`). The runner
scene loads `job.json` from the `--kinetic-job <path>` user argument (after `--`), instances
the shot scene, makes `job.camera` current, writes annotations, and quits after
`frame_count` frames. Failures exit non-zero and write `runtime_error.txt` in the run
directory.

### Godot runtime details (M2)

- **No trace:** a job without `trace_dir` runs with `tick = -1`, no state and no spans; its
  annotations have `"tick": null`.
- **Integers stay integers:** Godot's `JSON` parses every number as float, so the runtime reads
  job and trace files with its own reader (`json_lite.gd`). Annotation lines are written with
  sorted keys and full float precision.
- **Frame order:** `Kinetic._process` (priority -1000) updates frame, tick and the sampled
  trace; scene nodes update in their own `_process`; annotations are collected on
  `RenderingServer.frame_pre_draw`, after all processing and before the frame is drawn and
  written by Movie Maker. Each line therefore describes the pixels of its own frame (checked
  in `tests/integration/test_godot_render.py` against pulse pixel centroids).
- **Annotations:** only entities passed to `track` appear. `bbox` is the node's world AABB
  (merged over its visible `GeometryInstance3D` descendants) projected through the current
  camera, from the corners in front of it, clipped to the viewport and rounded to 0.01 px.
  It is `null`, with `visible: false`, when the node is hidden, entirely behind the camera or
  off screen. `state` is `{}` and `active_span` `null` when the entity is not alive or idle.
  `active_span` includes `planned_progress`; QA compares it with the trace (1e-6) when present.
  `labels` lists `[text, [x0, y0, x1, y1]]` for every registered `Control` visible in tree
  (unclipped, so QA can check safe areas).
- **Scenes:** the adapter copies the scene's directory (e.g. `scenes/godot/`, with everything
  under it) to `res://workspace/<same path>`; a scene must therefore live in a subdirectory of
  the film and refer to its own scripts by relative path (`path="failover.gd"`).
- **Quitting and errors:** the runner quits during the process step of frame
  `frame_count - 1`, which still draws and writes that frame, so a run without
  `--quit-after` also yields exactly `frame_count` frames. Exit codes: 2 bad or missing job,
  3 camera not found, 4 trace unreadable, 5 shot scene missing or not a `Node3D`; each also
  writes `runtime_error.txt`. The adapter additionally fails a run whose log contains
  `SCRIPT ERROR`.
- **Parity tool:** `godot --headless --path <project> --script
  res://addons/kinetic_runtime/tools/sample_trace.gd -- --trace <dir> --ticks 0,10,...` prints
  `KINETIC_SAMPLE {"<tick>": {"state": ..., "spans": [...], "last": {"<entity>": ...}}}`
  (`last`: the `last_span` view of every entity whose span has ended by that tick;
  `--headless` is fine here because Movie Maker is not used).

## Blender runtime (`renderers/blender/kinetic_bpy/`, GPL-2.0-or-later)

Blender runs in background mode: `blender -b --factory-startup --python
<runtime>/kinetic_bpy/run.py -- --kinetic-job <run_dir>/job.json`. The runtime never imports
`ostrium_kinetic`; it reads `job.json`, `trace.jsonl`, `spans.jsonl` and `trace.meta.json`
with the standard library (`json` keeps integers as integers) and samples them with the same
rules as `sim/trace.py` (state fold, active spans `begin <= t < end`, `progress`,
`planned_progress`, `last_span`). Frame `f` maps to a tick with the integer formula above.

A Blender shot scene is a Python module (path in the shot's `scene`, relative to the film
directory, e.g. `scenes/blender/structure.py`). It defines two functions and no other
entry point:

```python
def build(k) -> None:
    """Called once on an empty scene: build geometry, materials, lights, world and one camera
    object per framing camera name (e.g. "hero", "tall"). Read k.job, k.style(), and the whole
    trace (k.trace_events, k.spans, k.signals_in(-1, k.job["tick_end"])) to plan the shot."""


def frame(k) -> None:
    """Called before each frame renders, with k.frame, k.tick and the sampled trace current.
    Set object transforms, visibility and material values as a pure function of k.tick and
    k.frame. Never keep state between calls; frames may render in any order on retry."""
```

`k` (the `Kinetic` object passed in) offers: `job` (dict), `frame`, `tick`, `sim_seconds`,
`shot_progress`, `calm`, `evidence`, `time_label`, `style(key, default)`,
`state(entity)`, `alive(entity)`, `active_span(entity)`, `spans_active()`,
`last_span(entity)`, `signals_in(from_tick, to_tick)` — the same dictionaries as the Godot
runtime — plus `track(entity, obj)` to put an object's camera-space bbox (via
`bpy_extras.object_utils.world_to_camera_view` over its evaluated bounding box) into
`annotations.jsonl`, and `label(text, rect)` for text drawn in the frame (rect in pixels,
origin top-left).

The runner sets the render size from the job, colour management to AgX / sRGB, PNG RGB
8-bit output with metadata stamping off (M0: otherwise identical pixels hash differently),
makes `job.camera` the scene camera, then for each frame: set `scene.frame_set(f)`, update
`k`, call `frame(k)`, render a still to `frames/frame%08d.png`, append the annotation line.
EEVEE (`BLENDER_EEVEE`) is the engine for animation; `quality` draft vs high selects sample
counts and resolution of effects. Failures write `runtime_error.txt` and exit non-zero.

Scenes may import helper modules from their own directory (the runner puts the scene's
directory on `sys.path`) and may use only `bpy`, `bmesh`, `mathutils`, `bpy_extras` and the
standard library.
