---
name: godot-scene
description: Use when writing or changing a Godot shot scene for Kinetic (a .tscn plus GDScript under a film's scenes/godot/) - reading the Kinetic runtime singleton, framing cameras, positioning entities from trace state and span progress, track() and label() annotations, evidence badges, time-scale labels, the calm variant, lighting and materials for Intel Iris Xe Forward+, and debugging a scene with render.log and annotations.jsonl. Use after scene-architect has fixed the shot's sim, timemap, framings and checkpoints.
---

# Godot scene (draft, M2)

The trace decides what happened and when; the scene decides how it looks. A scene never
decides *what is true*. Contract: `docs/runtime-api.md`. Render and check the result with
`render-and-verify`. Visual rules: `docs/architecture-proposal.md` section 12.

## 1. What a shot scene is

- A `.tscn` at the shot's `scene:` path (relative to the film directory), e.g.
  `scenes/godot/failover.tscn`, with its scripts beside it.
- The root is a `Node3D`. It contains a `Camera3D` **named after every camera** in the shot's
  `framings` (`wide`, `tall`, `hero`, ...). The runner makes `job.camera` current; do not call
  `make_current()` yourself.
- The whole scene directory is hashed into the render key. Any file in it (a texture, a stray
  `.import`, a backup) re-renders every shot that uses it. Keep shared assets in a separate
  directory only if you accept that.
- The scene never sets output size, window size, stretch, anti-aliasing or the frame rate. The
  per-run project owns them (ADR 0005). The scene never quits; the runner quits after
  `frame_count` frames.

## 2. The frame clock

The `Kinetic` autoload runs before scene nodes (`process_priority` -1000) and updates once per
frame. In your `_process`, read it; never keep time yourself.

- **Never accumulate `delta`.** No `t += delta`, no timers, no `Tween`, no autoplaying
  `AnimationPlayer`, no `TIME` in shaders. Each frame must be a pure function of the frame index.
- Everything that moves is a function of `Kinetic.tick` / trace state (claim-bearing) or of
  `Kinetic.shot_progress` (illustrative). An `AnimationPlayer` is allowed only when driven by
  `seek(Kinetic.shot_progress * length, true)` each frame.
- Shader animation gets a uniform you set from `Kinetic.sim_seconds` or `Kinetic.shot_progress`.
- Avoid effects whose output depends on previous frames (TAA, SDFGI, auto exposure, motion
  blur from history, GPU particles): frame N then depends on history, and the first frames of a
  shot look different from the rest.

## 3. Claim-bearing versus illustrative

| Thing on screen | Source | Easing |
|---|---|---|
| An entity a claim or checkpoint is about (packet, link status, node role) | `Kinetic.state(id)`, `Kinetic.active_span(id)["progress"]`, `Kinetic.alive(id)` | **None.** Map progress linearly onto the path. Non-linear motion must come from a `curve:` span in the sim. |
| Instant events (fault, timeout, route change) | `Kinetic.signals_in(prev_tick, Kinetic.tick)` or a state change | A flash or marker may be styled freely, but no flashes faster than 3 Hz |
| Camera, ambient drift, materials, trails, background | `Kinetic.shot_progress` | Free (one documented curve family, e.g. `smoothstep`) |

- Position claim-bearing entities only from state and active-span progress. No smoothing, no
  lag, no "catch-up" lerp toward a target.
- Visibility follows the trace: show a packet while its span is active or while it is alive,
  hide it when `alive()` is false. Do not keep a node on screen "for continuity" after the trace
  destroyed the entity; a lost packet can be marked lost from its state, not kept travelling.
- Illustrative elements carry no quantity: no size, speed or colour that implies a value.

## 4. Annotations: `track()` and `label()`

The runtime writes one line per frame to `annotations.jsonl`; QA checks it against the trace
and the checkpoints (ADR 0006).

- `Kinetic.track(entity_id, node)` for **every entity a checkpoint references**, and for every
  claim-bearing entity on screen. Track the visible `VisualInstance3D` (the mesh), not an empty
  parent or a `Path3D`, so the bbox frames what the viewer sees. One node per entity: never
  track two entities with one node.
- `Kinetic.label(text, control)` for **every on-screen text**: badge, time-scale label, claim
  labels, captions drawn by the scene. Text is screen-space `Label`/`RichTextLabel` in a
  `CanvasLayer`, never `Label3D` or extruded text. If a label's text changes during the shot,
  call `label()` again with the new text; hide the control when the label is not on screen.
  (Confirm registration semantics in `renderers/godot/addons/kinetic_runtime/` before relying
  on them.)
- Any number in a label must be a claim's number, with the claim's precision and units.
- At most 4 labels on screen at once, including the badge.

## 5. Evidence badge and time-scale label

The job does not carry the evidence class or timemap label in contract 0.1, so the scene sets
them, **verbatim** from `film.yaml` and `docs/evidence-policy.md`:

- Badge per the shot's `evidence`: `measured` -> `MEASURED · <what>`, `sourced` ->
  `LIST PRICE · <region>` (or the source kind), `simulated` -> `SIMULATED · model, not <system>`,
  `hypothetical` -> `DESIGN TARGET · not measured`. `trace`, `computed` and `illustrative` have
  no badge. The badge sits in one fixed corner for the whole film and stays on screen for the
  whole shot.
- When the shot's timemap speed is not 1, show its `label` (e.g. `time slowed 3.4×`) for the
  whole shot, near the badge.
- Both go through `label()`.

## 6. Framings and safe areas

- Build each framing as a deliberate composition with its own camera, not a crop. Read
  `Kinetic.job["framing"]` (`"16:9"` or `"9:16"`) to switch HUD layout.
- The viewport is exactly `job.width` x `job.height`; lay out controls in those pixels.
- 9:16: no label in the top 14% or bottom 20% (captions live there). Cap height at least 40 px at
  1080x1920; 28 px at 1920x1080.
- Every checkpoint entity must be inside the frame and unoccluded in **every** framing at its
  checkpoint frame.

## 7. Calm variant

`Kinetic.calm` is true for `--calm` renders and shots with `variant_calm: true`. The calm
variant swaps camera moves for a lock-off (or cuts between fixed poses) and halves the amplitude
of illustrative motion. Claim-bearing motion is unchanged: it is the data.

## 8. Look defaults that hold up on Iris Xe, Forward+

Low-key, physically plausible, one motivated key light; emission only for things that carry
information.

- **Lights:** one `DirectionalLight3D` key with shadows (PSSM 2 splits, max distance close to
  the set size), one rim or fill `OmniLight3D`/`SpotLight3D` without shadows. Bake nothing.
- **Environment:** `WorldEnvironment` with a plain dark background or a dim sky, ambient light
  low, tonemap Filmic or AgX (record which in the style), glow on with a high threshold so only
  emissive data blooms. Auto exposure off. SSAO at low quality if needed. No SDFGI, VoxelGI,
  SSIL, SSR or volumetric fog: expensive on an iGPU and history-dependent.
- **Anti-aliasing:** leave it to the per-run project (MSAA). Do not enable TAA or FSR in the
  scene; TAA ghosts on thin lines.
- **Materials:** `StandardMaterial3D`, metallic 0, roughness 0.5-0.8, bevelled geometry for
  nodes. Colours come from style roles via `Kinetic.style("color.<role>", default)`: `ground`,
  `durable`, `cache`, `in_flight`, `fault`, `recovered`, `hypothetical`, `annotation`. Every
  role also has a non-hue encoding (dash, pattern, shape).
- **Text:** the style's OFL font, explicit size, an outline or plate for contrast of at least
  4.5:1.
- **Banned:** glowing circuit boards, hex grids, fake terminals, decorative equations, HUD
  clutter.

## 9. M0 facts that shape scenes

- Godot runs with a real window, never `--headless` (it crashes and writes no frames).
- Output size comes from the per-run project viewport, not `--resolution` or the window. A scene
  that resizes its window or viewport breaks the PNG size check.
- `--quit-after N` writes exactly N frames, `frame00000000.png` to `frame{N-1}`;
  `Engine.get_frames_drawn()` equals the frame index, which is what `Kinetic.frame` uses.
- The first render after a scene or shader change compiles shaders: about 0.65 s/frame cold
  against 0.15-0.25 s/frame warm. Slow is not hung.
- PNG encoding dominates render time for simple scenes; scene complexity is cheap until it is
  not, so check `seconds` in `shot.json` after adding effects.

## 10. Debugging

- `render.log` in the run directory has Godot's stdout and stderr: `SCRIPT ERROR`, missing
  nodes, failed resource loads. `runtime_error.txt` is written when the runtime aborts.
- A temporary `print("dbg ", Kinetic.frame, " ", Kinetic.tick, " ", Kinetic.active_span(id))`
  lands in `render.log`. Remove it afterwards (it changes the scene hash anyway).
- Compare `annotations.jsonl` line `f + 1` with what you expected at frame `f`: wrong `tick`
  means a clock problem; right tick but wrong state means the scene drew its own state; correct
  annotation but wrong picture means the tracked node is not the visible one.
- `--keep-frames` keeps every PNG of a shot so you can view the frames around an event.
- The editor has no job: compose in the editor, but verify only through `kinetic render`.

## 11. Example: a pulse on a link

`scenes/godot/failover.gd`, attached to the root of `failover.tscn`. Each link is a `Path3D`
whose curve runs from node A to node B, with a `Tube` mesh child.

```gdscript
extends Node3D

const LINKS := {"link:P": ^"LinkP", "link:S": ^"LinkS"}
const PULSES := {"packet:1": ^"Pulse1", "packet:2": ^"Pulse2", "packet:ack2": ^"PulseAck"}
const BADGE := "SIMULATED · model, not a real network"   # verbatim per evidence class
const TIME_LABEL := ""                                     # film.yaml timemap.label; empty at 1x
const MOVES := {  # camera -> [start, end, look_at]; illustrative, so easing is allowed
	"wide": [Vector3(0, 2.2, 7.0), Vector3(0.6, 1.8, 5.5), Vector3(0, 0.4, 0)],
	"tall": [Vector3(0, 4.0, 9.5), Vector3(0, 3.4, 8.5), Vector3(0, 0.4, 0)],
}

@onready var corner: Control = $HUD/Corner          # VBoxContainer: Badge, TimeScale
@onready var badge: Label = $HUD/Corner/Badge
@onready var time_label: Label = $HUD/Corner/TimeScale
var _link_mat := {}

func _ready() -> void:
	for id in LINKS:
		var tube: MeshInstance3D = get_node(LINKS[id]).get_node("Tube")
		_link_mat[id] = StandardMaterial3D.new()
		tube.material_override = _link_mat[id]
		Kinetic.track(id, tube)
	for id in PULSES:
		Kinetic.track(id, get_node(PULSES[id]))
	Kinetic.track("node:A", $NodeA/Mesh)
	Kinetic.track("node:B", $NodeB/Mesh)
	if Kinetic.job["framing"] == "9:16":
		corner.position.y = 0.16 * float(Kinetic.job["height"])   # below the top 14%
	badge.text = BADGE
	Kinetic.label(BADGE, badge)
	time_label.visible = TIME_LABEL != ""
	if time_label.visible:
		time_label.text = TIME_LABEL
		Kinetic.label(TIME_LABEL, time_label)

func _process(_delta: float) -> void:   # delta is never used
	for id in LINKS:
		_link_mat[id].albedo_color = _status_color(str(Kinetic.state(id).get("status", "")))
	for id in PULSES:
		var pulse: Node3D = get_node(PULSES[id])
		var span: Dictionary = Kinetic.active_span(id)
		pulse.visible = span.get("kind", "") == "transit"
		if pulse.visible:
			pulse.global_position = _on_link(span["via"], span["from"], span["progress"])
	_camera()

func _on_link(link_id: String, from: String, progress: float) -> Vector3:
	var path: Path3D = get_node(LINKS[link_id])
	var u := progress if from == "node:A" else 1.0 - progress   # linear: no easing
	return path.to_global(path.curve.sample_baked(u * path.curve.get_baked_length()))

func _status_color(status: String) -> Color:
	match status:
		"failed", "down":
			return Color.html(str(Kinetic.style("color.fault", "#d1495b")))
		"standby":
			return Color.html(str(Kinetic.style("color.ground", "#5c6370")))
		_:
			return Color.html(str(Kinetic.style("color.durable", "#3a7bd5")))

func _camera() -> void:
	var cam: Camera3D = get_node(str(Kinetic.job["camera"]))
	var m: Array = MOVES[cam.name]
	var s := 0.0 if Kinetic.calm else smoothstep(0.0, 1.0, Kinetic.shot_progress)
	cam.position = (m[0] as Vector3).lerp(m[1], s)
	cam.look_at(m[2])
```

What the example gets right: the pulse is placed only from the active span's `progress` and
`via`/`from` attributes; link colour comes only from trace state; the camera is a pure function
of `shot_progress` and holds still in the calm variant; every checkpoint entity is tracked by
its visible mesh; the badge and time-scale label go through `label()` and move out of the 9:16
unsafe band. A
failed link also needs its non-hue encoding (a dashed tube material), omitted here for length.
