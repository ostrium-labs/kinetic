# Quality assurance

A renderer exit code is never success. A shot counts only when its frames, its annotations and
its mezzanine pass QA, and a film only when its delivery passes too. Even then, QA proves the
picture is *structurally* right and *says* the right thing (ADR 0006); whether it *shows* the
right thing is decided by viewing stills and contact sheets (`render-and-verify` skill) and by
an independent critique (`film-critic` agent).

Sources of truth: `src/ostrium_kinetic/qa/` (checks), `src/ostrium_kinetic/pipeline.py`
(when they run). If this page and the code disagree, the code wins; fix this page.

## Where QA runs

| Stage | Command | Checks | Written to |
|---|---|---|---|
| Per shot, after the renderer exits | `kinetic render` (and `build`) | `frames.*`, `annotations.*`; then, only if none failed, stills are kept, the mezzanine is encoded and `mezzanine` runs | `.kinetic/shots/<key16>/qa.json`; status in `shot.json` |
| Per film, after assembly | `kinetic assemble`, `kinetic build` | every shot's checks, `shot.status`, `delivery.*`, `media.black`, `media.freeze` | `out/<film_id>/<profile>/qa/report.json` |
| Re-report from the cache | `kinetic qa` | every shot's stored checks and `shot.status`; no delivery or black/freeze checks (nothing is assembled) | `out/<film_id>/<profile>/qa/report.json` (overwritten) |

`kinetic qa`, `assemble` and `build` print the report (failures first) and exit **1 if any
check has status `fail`**, 0 otherwise. Warnings never change the exit code, but each one needs
an answer before a gate.

A shot whose frame or annotation checks fail stops there: no stills, no mezzanine, status
`failed`, and its `frames/` directory is kept for diagnosis. A passing shot's PNGs are deleted
after the mezzanine unless `--keep-frames` is given.

## Report format

`qa/report.json` (canonical JSON):

```json
{
  "film_id": "kinetic.smoke-failover",
  "profile": "preview",
  "checks": [
    {"id": "frames.count", "shot_id": "S1", "status": "pass", "detail": "105 PNGs, expected 105",
     "data": {"found": 105, "expected": 105}}
  ]
}
```

- `id`: the check id below. `shot_id`: `null` for film-level checks.
- `status`: `pass`, `warn` or `fail`. Only `fail` blocks.
- `detail`: one human-readable line, listing at most five offenders.
- `data`: machine-readable specifics (offending frames, labels, ranges; capped at 50 items).

## Checks

### Frames (per shot, `qa/frames.py`)

| Id | Fails or warns when |
|---|---|
| `frames.count` | the number of PNGs in `frames/` is not the job's `frame_count`, or `frames/` is missing (fail) |
| `frames.numbering` | `frame00000000.png` .. `frame{N-1:08d}.png` are not all present, or there are extra or misnamed PNGs (fail) |
| `frames.stray_files` | non-PNG files sit in `frames/`, e.g. Godot's `frame.wav` (warn; only emitted when present) |
| `frames.size` | any frame's PNG IHDR width x height differs from the profile size (fail). Godot output size comes from the per-run project viewport (ADR 0005) |
| `frames.integrity` | a frame is not a PNG or is truncated (no `IEND` chunk) (fail) |

### Annotations (per shot, `qa/annotations.py`; see ADR 0006)

| Id | Fails or warns when |
|---|---|
| `annotations.structure` | `annotations.jsonl` is missing, a line does not parse as a `FrameAnnotation`, there are not exactly `frame_count` lines, a line is out of frame order, or a line's `tick` differs from `tick_start + ((tick_end - tick_start) * f) // frame_count` (fail) |
| `annotations.parity` | for any annotated entity on any frame, the annotated `state` differs from the trace's `state_at(tick)` (same keys, equal values), an entity is annotated with state while the trace says it is not alive, or the annotated `active_span` differs in `span_id` or in `progress` by more than 1e-6 from the trace's (fail). Skipped for shots without a trace |
| `annotations.checkpoint` | one check per `checkpoints:` entry: at `at_frame`, an expected entity is not annotated, an expectation does not hold on the *annotated* data, or an expected entity is annotated but not `visible` (fail) |
| `annotations.layout.bounds` | a label's rect leaves the frame or is inverted (fail) |
| `annotations.layout.safe_area` | 9:16 only: a label's rect enters the top 14% or bottom 20% of the frame (fail) |
| `annotations.layout.label_size` | a label's rect is less than 28 px high per 1080 px of frame height (warn) |
| `annotations.layout.clutter` | more than 4 labels on a frame (warn) |

`kinetic validate` evaluates the same checkpoints on the trace alone. A checkpoint that passes
`validate` and fails QA means the renderer's testimony disagrees with the trace; one that fails
both means the trace (or the checkpoint) changed.

### Mezzanine (per shot, `qa/media.py`)

| Id | Fails when |
|---|---|
| `mezzanine` | ffprobe cannot read `mezzanine.mkv`, or it is not FFV1, not `bgr0` (RGB), not the shot's size, or not exactly `frame_count` frames |

### Shot status (per shot, film report)

| Id | Fails when |
|---|---|
| `shot.status` | a plan shot is not `mezzanine_ok` for the current render key: never rendered, rendered for older inputs, or failed. `detail` carries the shot's error |

### Delivery (film-level, `qa/media.py`; `assemble` and `build` only)

| Id | Fails when |
|---|---|
| `delivery.probe` | ffprobe cannot read the delivery file (`film.mp4` or `social-9x16.mp4`) |
| `delivery.size` | width x height is not the profile's |
| `delivery.fps` | the frame rate is not exactly the film's (a fraction, not a float) |
| `delivery.frames` | the frame count is not the plan's total |
| `delivery.duration` | duration differs from frames / fps by more than one frame |
| `delivery.pix_fmt` | not `yuv420p` |
| `delivery.color` | any of `color_space`, `color_primaries`, `color_transfer` is not `bt709`, or `color_range` is not `tv`. M0 found that ffmpeg drops primaries and transfer unless the chain uses `setparams` |

### Black and frozen stretches (film-level, `assemble` and `build` only)

| Id | Warns when |
|---|---|
| `media.black` | `blackdetect` finds a black stretch of at least 0.5 s (pixel threshold 0.10); `data.ranges` lists them in seconds |
| `media.freeze` | `freezedetect` finds a stretch of at least 2 s with less than 0.001 noise; an intended lock-off on a waiting state can trigger it, so explain it rather than add motion |

Both fail (instead of warn) if ffmpeg itself cannot run.

### Not checked by QA in M2

These are covered by human and `film-critic` review until a check exists:

- that every number in a label maps to a claim (labels carry text and rect, not a claim id);
- that the evidence badge and time-scale label are present and correct;
- that illustrative elements encode no quantity;
- text contrast, occlusion of a tracked entity by other geometry, flashes faster than 3 Hz;
- audio, loudness and captions (M5).

## Gates

**Gate A, engineering (M2a).** `examples/smoke-failover` passes:

- `kinetic build examples/smoke-failover --profile preview` exits 0 and `qa/report.json` has no
  `fail`: 105 frames at 1280x720, annotation parity on every frame, all four checkpoints
  (frames 30, 40, 60, 90), delivery 3.5 s +-1 frame at 30 fps with BT.709 tags.
- A rerun is a cache hit for every shot (no renderer started).
- A render killed mid-shot leaves `shot.json` at `rendering` or `failed`, and the next run
  re-renders that shot cleanly.
- The checkpoint stills and contact sheet have been viewed and show the four beats.
- Trace sha256 matches `tests/golden/smoke-failover.trace.sha256`.

**Gate B, creative (M2b).** `examples/lookdev-failover` at 1080p:

- passes Gate A's mechanical checks for its own shot (360 frames, checkpoints 62, 128, 226,
  308; the `time slowed 3.4×` label; the `SIMULATED` badge);
- the `film-critic` craft pass reports no `blocker`;
- the owner approves the clip and stills against the visual principles (architecture proposal
  section 12). If Gate B fails, iterate the look or revisit the renderer choice before M3.

A passing report is necessary for both gates and sufficient for neither.

## Determinism

What Kinetic guarantees, and what it does not:

- **Byte-identical across machines and runs:** `trace.jsonl` (and spans, meta) for the same sim
  source, params and seed; `render-plan.json` and cache keys for the same inputs; the
  annotation *data the trace determines* (tick, state, span id). Annotation `progress` is
  compared to the exact rational value within 1e-6, and bboxes depend on the renderer's
  projection, so annotations are compared semantically, not by file hash, across machines.
- **Identical per machine class:** frame pixels. M0 measured byte-identical PNGs across repeated
  Godot (Forward+ and Compatibility), Blender (with PNG metadata off) and Manim runs on one
  machine. GPU rasterisation, drivers and EEVEE are not guaranteed bit-exact across machines,
  so pixel goldens (SSIM >= 0.98 at 256 px, smoke test only) apply to one machine class.
- **Lossless after the renderer:** FFV1 mezzanines and their concat are bit-exact to the
  frames; only the delivery encode is lossy.

Cross-machine comparison therefore uses traces and annotations, never pixels. A changed trace
hash for the smoke test is a contract change, not a test update.
