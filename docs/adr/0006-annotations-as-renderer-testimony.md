# ADR 0006: Annotations as renderer testimony

**Status:** Accepted (M2, 2026-10-30)

## Context

Kinetic's promise is that every moving thing on screen is either data or labelled as not data.
QA therefore has to answer, for every shot, "does the picture show what the trace says?", and
it has to answer it in CI, on a laptop, without a human.

The options were:

1. **Computer vision on the frames:** detect entities, OCR labels, infer positions and colours.
   Fragile, slow, style-dependent, and wrong in exactly the cases that matter (a styled failed
   link, a small label, an occluded packet).
2. **Pixel goldens:** compare frames to approved references. M0 showed pixels are identical run
   to run on one machine, but GPU rasterisation is not bit-exact across drivers, and a golden
   says nothing about *why* a frame is right. Goldens also freeze the look, which is the part
   that should iterate.
3. **Renderer testimony:** the renderer runtime records, per frame, what it drew and from which
   trace values, in a format the core can check exactly against the trace.

## Decision

Renderer runtimes emit `annotations.jsonl`: one `FrameAnnotation` (`src/ostrium_kinetic/job.py`)
per frame, in frame order, holding

- `frame` and the `tick` the runtime sampled;
- for every tracked entity: the `state` it read, the `active_span` it used (span fields, attrs
  and `progress`), its screen-space `bbox`, and whether it is `visible` in the frame;
- every on-screen label: its text and screen rect.

The runtime, not the scene, writes the file. Scenes declare what is claim-bearing with
`track(entity, node)` and `label(text, control)` (`docs/runtime-api.md`). The runtime is
Kinetic-shipped code whose hash is part of every render key.

QA checks the testimony (`docs/qa.md`):

- **structure:** one line per frame, ticks equal to the exact frame-to-tick mapping;
- **parity:** annotated state and active spans equal the reference sampler's
  (`sim/trace.py`) at that tick, progress within 1e-6;
- **checkpoints:** the film's checkpoints hold on the annotated data, and their entities are
  visible;
- **layout:** labels inside the frame and the 9:16 safe area, large enough, at most four.

## Consequences

- QA can verify the picture against the trace without computer vision: exact, fast, and
  independent of style. A look can change freely while the same checkpoints keep holding.
- Checkpoints are renderer-independent. The M4 test of the trace contract is that the same
  trace rendered by Godot and by Blender passes the same checkpoints on their annotations.
- Cross-machine comparison uses annotations, not pixels; pixel goldens stay limited to the
  smoke test on one machine class.
- Most "the scene invented state" bugs become parity failures with a frame, tick and entity in
  the message, instead of something a reviewer might notice.
- **Testimony is not observation.** An annotation says what the renderer read and where the
  node projects; it does not prove the entity is unoccluded, legible, correctly coloured, or that
  the tracked node is the one the viewer sees. Viewing stills and contact sheets, and the
  `film-critic` review, remain required. A passing report is necessary, never sufficient.
- QA cannot check what is not tracked. An entity a checkpoint references but the scene did not
  track fails as "not annotated"; claim-bearing entities with no checkpoint rely on scene
  discipline (`godot-scene` skill) and review.
- Labels carry no claim id in contract 0.1, so "every number cites a claim" is still a review
  check. Adding claim ids to labels is a candidate for a later contract revision.
- Every runtime (Godot now; Blender and Manim in M4) must implement the same sampler, projection
  to a top-left-origin pixel bbox, and the integer frame-to-tick mapping. Divergence from
  `sim/trace.py` shows up as parity failures against a correct scene.
- Annotation files add a few kilobytes per frame and are kept with the shot's run directory.
