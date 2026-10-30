---
name: manim-scene
description: Use when writing or changing a Manim shot scene for Kinetic (a Python module under a film's scenes/manim/ defining class Shot(KineticScene)) - charts, timelines, arithmetic and algorithm-on-data shots driven by the trace, build()/frame() as pure functions of self.k, the 1080 px layout grid (k.px), Pango text without LaTeX (self.text/self.markup, installed fonts), track() and label() annotations, evidence badge and time-scale label, and debugging with render.log, runtime_error.txt and annotations.jsonl. Use after scene-architect has fixed the shot's sim, timemap, framings and checkpoints.
---

# Manim scene (M4)

The trace decides what happened and when; the scene decides how it looks. Contract:
`docs/runtime-api.md` ("Manim runtime"). Render and check with `render-and-verify`. Worked
example: `examples/manim-trace-timeline/scenes/manim/timeline.py`.

Setup once: `uv sync --extra manim` (Manim CE 0.22.1 in Kinetic's venv). Without it the
adapter stops with "Manim is not importable ... install with `uv sync --extra manim`".

## 1. What a shot scene is

- A `.py` file at the shot's `scene:` path, in a subdirectory of the film
  (`scenes/manim/<name>.py`), defining `class Shot(KineticScene)` with `build()` and `frame()`.
  Helpers beside it are importable. The whole directory is in the render key.
- `from ostrium_kinetic.manim_runtime.scene import KineticScene`. Import anything from
  `manim` except what needs LaTeX (section 4).
- Never override `construct()`, never call `self.play()` or `self.wait()`, never use
  `Animation`s, updaters, `ValueTracker` animations or `always_redraw` driven by time. The base
  class renders exactly one image per frame with `wait(1/fps, frozen_frame=False)`; a frozen
  wait writes ONE image however long it is (M0), and any `play()` adds images and fails the
  frame-count check (exit 6).
- The scene never sets resolution, frame rate, background or output format. Background is the
  style token `background` (default `#0E1116`).

## 2. build() and frame()

```python
class Shot(KineticScene):
    def build(self) -> None:  # once: static mobjects, layout planned from the whole trace
        ...
    def frame(self) -> None:  # every frame: a pure function of self.k
        ...
```

- `build()` may read the whole trace (`self.k.spans`, `self.k.signals_in(-1, self.k.tick_end)`)
  to plan lanes, axes and scales. It must not use the future to *show* anything early.
- `frame()` sets geometry, colour and text from `self.k.tick`, `self.k.state(id)`,
  `self.k.active_span(id)`, `self.k.last_span(id)`, `self.k.signals_in(...)`. No state kept
  between calls; no `+=` on positions. A simple pattern: keep one `VGroup` (`self.dynamic`),
  empty it at the start of `frame()` and add freshly built mobjects.
- Claim-bearing geometry is linear in ticks or progress (no easing): a bar from
  `x(begin_tick)` to `x(min(tick, end_tick))`, a marker at `planned_progress`. An interrupted
  span (`outcome` lost/aborted) stops at `end_tick`; show its planned end
  (`planned_end_tick`) as a ghost, never as reached.
- Illustrative motion (camera drift, fades of chrome) may use `self.k.shot_progress`, and holds
  still when `self.k.calm`.

## 3. Layout grid and typography

- Design on a 1080-high pixel grid: `self.k.px(n)` converts grid px to scene units at any
  output size. Frame width in grid px is `1080 * k.width / k.height` (1920 for 16:9). Map grid
  (x, y) with origin top-left to a scene point as
  `((x - fw/2) * k.px(1), (540 - y) * k.px(1), 0)`.
- Use a few constants for margins (e.g. 144 px sides, 96 px top), one baseline rhythm, and
  align text by its ink box (top-left / top / top-right), not by centre.
- `self.text(s, size, role="sans"|"mono", color=...)` and `self.markup(markup, size, ...)`:
  `size` is the **capital height in px at 1080**. Labels 30 (QA minimum is 28), scale numerals
  28. One `MarkupText` per line keeps one baseline; mix colours and fonts with
  `<span foreground="#..." font_family="...">`, and `html.escape()` data first.
- Fonts come from `self.font(role)` (style tokens `font_sans`, `font_mono`; defaults
  "Noto Sans", "DejaVu Sans Mono"). A missing font fails the render (exit 3) instead of
  falling back to a serif. Noto Sans has no arrows (U+2192): put them in a mono span.
- Pango shaping is the slow part (~0.1-0.3 s per new string). Cache text mobjects by string and
  re-place the same object on later frames (do not `.copy()`: a deep copy costs as much as
  shaping). A cached object can appear only once per frame.
- Readability: text on a plate (`Rectangle` filled with the background) when lines cross it;
  contrast at least 4.5:1; numbers in a mono face so they do not jitter as they change.

## 4. No LaTeX

LaTeX is not installed on Kinetic machines. These fail: `MathTex`, `Tex`, a default
`DecimalNumber`, `Axes(include_numbers=True)`, `unit=`/`show_ellipsis=` options. Use
`Text`/`MarkupText`, `DecimalNumber(mob_class=Text)`, `label_constructor=Text`, or build
axes from `Line`s and `self.text()` numerals.

## 5. Annotations: track() and label()

- `self.k.track(entity, mobject)` for every entity a checkpoint references and every
  claim-bearing mark. Track the mark the viewer reads (the bar, the dot), not a group with its
  caption. If you rebuild mobjects each frame, re-track each frame; track an empty `VGroup()`
  when the entity has nothing on screen (it is then `visible: false`).
- `self.k.label(text, mobject)` for the badge and every caption or value label. In `build()` it
  persists; in `frame()` it counts for that frame only. QA: inside the frame, at least 28 px
  at 1080 (ink height), no label over a tracked mark, at most 4 labels per frame plus the badge
  and time label. Scale numerals of an axis are part of the axis graphic and are not labels.
- Every number in a label comes from the trace (ticks, state) or a claim, with units.

## 6. Evidence badge and time-scale label

Build them from `self.k.evidence.upper()` and `self.k.time_label` (never hard-coded), as one
label that starts with the evidence class (QA treats it as chrome), in a fixed corner for the
whole shot. For 9:16 keep every label out of the top 14% and bottom 20%.

## 7. Debugging

- `render.log` (run directory) has the subprocess output, including Python tracebacks;
  `runtime_error.txt` has the one-line reason and the exit code meaning (`docs/runtime-api.md`).
- Iterate fast: copy the run's `job.json`, lower `frame_count`/size and `out_dir`, and run
  `uv run python -m ostrium_kinetic.manim_runtime.run --kinetic-job <copy>/job.json`.
- `kinetic render ... --keep-frames` keeps every PNG; view stills with the Read tool.
- Cost: about 0.3-0.5 s per 720p frame for a text-heavy chart plus ~10 s to import Manim.
