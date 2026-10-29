---
name: render-and-verify
description: Use when rendering a Kinetic film or shot and deciding whether the result is right - running kinetic render/assemble/qa/build, reading shot.json, qa.json and qa/report.json, viewing checkpoint stills and contact sheets, diagnosing QA failures (frame count, PNG size, annotation parity, invisible checkpoint entities, safe areas, BT.709 tags), and re-rendering one shot. Use after kinetic validate passes and a shot's scene exists.
---

# Render and verify (draft, M2)

The CLI renders, encodes and runs structural and semantic QA. You decide whether a shot is
right, from the QA report and from looking at the pictures. Design background:
`docs/architecture-proposal.md` section 9; check semantics: `docs/qa.md`; runtime contract:
`docs/runtime-api.md`. Scene authoring is `godot-scene`; what a shot must show is set by
`film-director` and `scene-architect`.

## 0. The hard rule

**A zero exit code is never success.** A shot is done only when:

1. `out/<film_id>/<profile>/qa/report.json` has no `fail` (read it; do not infer it from the
   exit code), and
2. you have **viewed** the shot's stills and contact sheet with the Read tool and they show the
   shot's `intent` and every checkpoint beat, with the right badge and labels.

When you report a shot as passing, list the image paths you looked at and what you saw at each
checkpoint frame. If you did not look, say "not visually verified". Never render `production`
before the same shots pass `preview` QA and visual review.

## 1. The loop

```bash
uv run kinetic validate <film>                          # contract, evidence, sims, checkpoints vs trace
uv run kinetic render <film> --profile preview          # every stale shot; cached shots are skipped
#   read shot.json + qa.json, VIEW stills (sections 2-4)
#   fix the scene or the sim (sections 5-6), then re-render only that shot:
uv run kinetic render <film> --profile preview --shot S1
uv run kinetic assemble <film> --profile preview        # master.mkv, film.mp4, contact/, full QA, provenance
#   VIEW out/<film_id>/preview/contact/*.png
uv run kinetic qa <film> --profile preview              # re-report shot QA from the cache; exit 1 on any fail
uv run kinetic build <film> --profile production        # render + assemble + qa + provenance
#   read qa/report.json, VIEW the production contact sheets, check provenance.json
```

- `--calm` renders the reduced-motion variant (a shot with `variant_calm: true` always does).
  The calm variant has its own cache key; verify it like any other render, and pass `--calm` to
  `assemble`, `qa` and `build` too.
- `assemble` needs every shot of the plan at `mezzanine_ok`. After `--shot`, run `render`
  without `--shot` (cached shots are free) before `assemble`.
- `assemble` and `build` write the full report, including `delivery.*` and `media.*` checks.
  `kinetic qa` alone renders and assembles nothing and reports only per-shot checks plus
  `shot.status`, overwriting `qa/report.json`; re-run `assemble` before reading delivery results.
- Then hand the outputs to the `film-critic` agent (section 8). Final approval is the owner's.

## 2. Find a shot's run directory

Each render lives in `.kinetic/shots/<key16>/`, named by the first 16 hex digits of its render
key. A changed shot gets a new directory; old ones stay until cleaned. To find the current one:

- `kinetic render`, `assemble` and `build` print `S1: <status>  <run_dir>` for every shot, or
- `Grep` for `"shot_id": "S1"` in `.kinetic/shots/*/shot.json` and take the match whose
  `render_key` is current (the most recently modified one, if you just rendered).

| File | What it tells you |
|---|---|
| `job.json` | what the renderer was asked for: size, frame_count, camera, framing, tick range, calm |
| `shot.json` | `status` (`rendering` / `frames_ok` / `mezzanine_ok` / `failed`), `error`, wall `seconds`, renderer version |
| `qa.json` | per-shot checks: `frames.*`, `annotations.*`, `mezzanine`, each with `detail` and `data` |
| `render.log` | Godot stdout/stderr: `SCRIPT ERROR`, `push_error`, shader compile, crashes |
| `runtime_error.txt` | present only when the runtime aborted; read it first |
| `annotations.jsonl` | line `f + 1` is frame `f`: tick, tracked entities (state, active_span, bbox, visible), labels |
| `stills/frame%08d.png` | first, middle, last and every checkpoint frame |
| `contact.png` | up to 16 evenly spaced frames, 4 columns, 320 px thumbnails (written by `assemble`) |
| `frames/` | all PNGs; kept only on failure or with `--keep-frames` |
| `mezzanine.mkv` | lossless FFV1 RGB of the whole shot |

Film outputs: `out/<film_id>/<profile>/{master.mkv, film.mp4 | social-9x16.mp4,
contact/<shot>.png, qa/report.json, provenance.json}`.

## 3. Read the numbers first

1. `shot.json`: if `status` is `failed`, the first three failing check details are in `error`.
2. `qa.json` (shot) or `qa/report.json` (film): read every `fail` and every `warn`. A warning
   is a question you must answer, not noise.
3. For each checkpoint in `film.yaml`, open the matching line of `annotations.jsonl` and
   confirm the `tick`, the entity's `state` / `active_span`, `visible: true` and a sane `bbox`.

## 4. Look at the pictures

Use the Read tool on each PNG; it displays the image. Look, then compare against the shot's
`intent`, its checkpoints and `docs/evidence-policy.md`.

- **Checkpoint stills** (`stills/frame%08d.png` at each `at_frame`): the asserted beat is
  visible, not only true in the trace. At the frame where `link:P` is `failed`, the link reads as
  failed (fault colour plus its alternate encoding: dash or pattern), and the packet is where its
  span progress puts it.
- **The checkpoint bbox matches the picture:** the annotated bbox should frame the entity you
  see. A bbox on an empty area means the tracked node is not the visible one.
- **First, middle, last stills:** no black or grey frame, no camera inside geometry, the evidence
  badge present for `measured`, `sourced`, `simulated` and `hypothetical` shots, the time-scale
  label present whenever the timemap speed is not 1.
- **Contact sheet:** the story reads left-to-right, top-to-bottom; motion is continuous; nothing
  pops; camera moves only to reveal something. Thumbnails are 320 px wide: judge text
  legibility on the full-size stills, not on the sheet.
- **Labels:** at most 4 on screen; every number is a claim's number with its precision and units;
  text is legible at the delivery size; in 9:16, nothing sits in the top 14% or bottom 20%.
- **Illustrative elements** encode no quantity (no size, speed or colour that implies a value).

If a picture disagrees with a passing QA report, the picture wins: QA checks what the renderer
says it drew (ADR 0006), you check what it drew.

## 5. Common failures

Check ids and semantics: `docs/qa.md`. Frame and annotation failures stop the shot before the
mezzanine, so its `frames/` directory is kept for you to view.

| Symptom (check id) | Usual cause | Fix |
|---|---|---|
| Frame count or numbering (`frames.count`, `frames.numbering`) | Godot stopped early: script error, crash, or a scene that calls `get_tree().quit()`; or gaps from an interrupted run | Read `runtime_error.txt`, then `render.log` for the first `SCRIPT ERROR`. Remove any quit logic from the scene; the runner quits after `frame_count` frames. Re-render the shot. |
| PNG IHDR size differs from the profile (`frames.size`) | Output size comes from the per-run project viewport. A scene script changed the window or viewport size, content scale or stretch, or renders through its own SubViewport | Remove the size change from the scene. Never "fix" it with `--resolution`. Check the viewport line in `render.log` (ADR 0005). |
| Truncated PNG (`frames.integrity`) | The writer was killed mid-frame, or the disk filled | Check free disk; re-render the shot. |
| Stray files in `frames/` (`frames.stray_files`, warn) | Godot's `frame.wav`, or debug output written next to the frames | Harmless if it is only `frame.wav`; otherwise stop the scene writing files. |
| Annotation lines missing, out of order, or with the wrong tick (`annotations.structure`) | Runtime aborted mid-shot; or the frame-to-tick mapping is off (warm-up frame counted, off-by-one) | Same as frame count. A tick mismatch on every frame is a runtime bug; report it. |
| Annotation parity failure (`annotations.parity`): annotated state, span id or span progress differs from the trace at that tick | The scene invented state or sampled the wrong tick: it kept its own timer or "last status", accumulated `delta`, used `AnimationPlayer`/`Tween` playback, or annotated an entity the trace says is not alive. Less often, the runtime sampler disagrees with `sim/trace.py` | Read everything from `Kinetic` each frame (`godot-scene`). If the scene is clean and the runtime is wrong (span end is exclusive, `curve:` keys, step progress is 0), report a runtime bug; do not patch around it in the scene. |
| Checkpoint fails on value (`annotations.checkpoint`: `expected ..., got ...`) | Either the trace changed (sim or params edited) or the checkpoint is on the wrong frame | Run `kinetic validate`; it evaluates the same checkpoints on the trace alone. If validate passes and QA fails, the annotation disagrees with the trace: see parity. |
| Invisible checkpoint entity (`annotations.checkpoint`: `entity not annotated` or `is shown nowhere on screen`) | Not annotated: the entity is not `track()`ed. Not shown: its node is hidden, off-frame, behind the camera, or not framed by this framing's camera (often the 9:16 `tall` camera) | Track it; fix the framing. Move the checkpoint only if it sits on the wrong beat, and then re-run `validate`. |
| Label outside the frame (`annotations.layout.bounds`) or the 9:16 safe area (`annotations.layout.safe_area`) | Layout built for 16:9 and reused for 9:16; label anchored to a moving entity | Lay out per `Kinetic.job["framing"]`; in 9:16 keep labels between y 14% and 80% of the height; clamp anchored labels. |
| Label too small or too many labels (`annotations.layout.label_size`, `annotations.layout.clutter`, warn) | Label rect under 28 px high per 1080 px of frame height; more than 4 labels on a frame | Enlarge the font or cut labels. A warning still needs an answer in your report. |
| Unclaimed number on screen (no QA check in M2; you and the critic catch it) | On-screen text contains a number with no claim behind it | Remove it or ask the film-director for a claim. Never invent a number to fill a label. |
| BT.709 tags missing (`delivery.color`) | Delivery encode lacks `setparams`; ffprobe shows `unknown`/missing primaries or transfer | A media-module bug, not a scene bug. Report it; do not hand-encode a replacement. |
| Delivery size, fps, frames, duration, pix_fmt (`delivery.*`) | Concat of mezzanines with mismatched sizes or rates; encode error | Read the `detail`; check every shot's `mezzanine` check; report media bugs. |
| Black or frozen range (`media.black` >= 0.5 s, `media.freeze` >= 2 s, warn) | Camera inside geometry, lights off, scene failed to instance, or a long static hold | Black: fix the scene. Freeze: if the hold is intended (a lock-off on a waiting state), say so in your report; never add motion that does not come from the trace. |
| `shot.status` fail | The shot is not rendered for the current inputs, or it failed | Run `kinetic render` for that shot and read its `qa.json`. |
| `mezzanine` fail | FFV1 encode failed or disagrees with the frames (codec, `bgr0`, size, count); disk full | Read the structured log; check free disk; re-render with `--force`. |
| First render very slow | Cold shader compile on Forward+ (M0: 0.65 s/frame cold vs 0.15-0.25 warm) | Wait. It is not a hang unless `render.log` stops growing. |

## 6. Fix, then re-render one shot

- Decide where the defect lives. Wrong story (wrong order, wrong timing, wrong outcome) is a
  sim or timemap problem: hand it to `scene-architect`, re-run `kinetic validate`. Right story,
  wrong picture is a scene problem: `godot-scene`.
- Never edit a sim to make a picture or a published number come out right.
- Re-render with `--shot <ID>`. The render key covers the shot spec, trace, the whole scene
  directory, runtime, renderer version and calm flag, so an edited scene re-renders by itself.
  Use `--force` only when the inputs are unchanged and you suspect the environment (driver,
  shader cache).
- Re-read `qa.json` and view the new stills. Do not reuse conclusions from the previous run.

## 7. Production and provenance

1. Only after preview QA has no `fail` and you have viewed every shot.
2. `uv run kinetic build <film> --profile production` (or `--profile social` for 9:16, which
   writes `social-9x16.mp4`).
3. Read `qa/report.json` again: the checks are the same, but label-size thresholds scale with
   the frame height and every shot was re-rendered, so nothing carries over from preview.
4. View `out/<film_id>/production/contact/*.png`.
5. Open `provenance.json`: every shot has a render key, trace sha256, scene sha256 and renderer
   version; tool versions are present; outputs are listed with hashes.

## 8. Independent review

Spawn the `film-critic` agent with **only paths**: the `film.yaml` (and claims file),
`qa/report.json`, the contact sheets and the stills directories. Do not include your reasoning,
your intent for the shot or what you already fixed; the critic must not be anchored on it.
Treat every `blocker` as a QA failure. Report `major` findings to the owner with your
proposed fix.

## 9. Logs

- Per shot: `.kinetic/shots/<key16>/render.log` and `runtime_error.txt`.
- Pipeline: structured JSON lines in `.kinetic/logs/` (render events `shot_done` and
  `render_failed` carry `film`, `profile`, `shot`, `key`, `status` or `error`).
- Plan: `render-plan.json` under `.kinetic/` (which shots are cached or stale, and why).
- Godot user data and shader cache: `%APPDATA%\Godot\app_userdata\<project>` unless the adapter
  isolates it per run.

## 10. Disk hygiene

- Frames are deleted once a shot reaches `mezzanine_ok`. Use `--keep-frames` only while
  debugging a specific frame range; 1080p PNGs are 2-4 MB each.
- A failed shot keeps `frames/` for diagnosis. Once you have read what you need, the next
  render clears it.
- Mezzanines are lossless FFV1: any frame can be recovered from `mezzanine.mkv` exactly, so do
  not keep PNGs "just in case". Extract a single frame into your scratchpad when needed:
  `ffmpeg -i mezzanine.mkv -vf "select=eq(n\,40)" -frames:v 1 <scratchpad>/f40.png`.
- Run directories for stale keys accumulate under `.kinetic/shots/`. Ask the owner before
  deleting cache directories.
