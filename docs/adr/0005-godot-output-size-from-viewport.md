# ADR 0005: Godot output size comes from the project viewport

**Status:** Accepted (M1, from M0 measurements, 2026-10-30)

## Context

Godot Movie Maker writes frames at the size of the rendered viewport. Godot clamps window
sizes to the physical display, and `--headless` disables rendering. Kinetic needs exact
1920x1080 (and 1080x1920) frames on a laptop whose display is 1920x1080.

## Decision

- The per-run Godot project sets the output size in `project.godot`:
  `display/window/size/viewport_width/height` = the profile resolution,
  `display/window/stretch/mode = "viewport"`, and a small
  `window_width_override/height_override` (640x360) for the visible window.
- Kinetic never relies on `--resolution` or the window size for output size.
- Godot runs as `Godot_v4.7.2-stable_win64_console.exe` (stdout captured), never with
  `--headless`, with `--rendering-method forward_plus`, `--write-movie <abs>/frame.png`,
  `--fixed-fps N` and `--quit-after N`.
- QA asserts the actual PNG dimensions of every shot.

## Consequences

M0 measurements (Godot 4.7.2, Iris Xe, Vulkan 1.4 for Forward+):

- With the viewport settings: 30 frames at 1920x1080. With only `--resolution 1920x1080`:
  1152x648. The window itself was clamped to 1920x1055.
- `--quit-after N` writes exactly N frames, `frame00000000.png` to `frame{N-1:08d}.png`.
- `--headless` crashed (signal 11) and wrote no frames.
- Frames were byte-identical across 3 forward_plus runs and 2 gl_compatibility runs.
- Warm runs took 0.15-0.25 s/frame, dominated by PNG encoding; a cold forward_plus run took
  0.65 s/frame. Kinetic does a warm-up run per fresh shader cache.
- A stray `frame.wav` is written next to the frames and must be ignored or deleted.
- A real window opens on Windows. Linux CI rendering needs a display server (deferred to M6).
