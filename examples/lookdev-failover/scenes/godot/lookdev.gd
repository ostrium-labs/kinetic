extends Node3D
## Look-development treatment of the failover trace (shot L1, examples/lookdev-failover).
##
## Trace-driven: packet positions, conduit core glow, link status stripes, fault / timeout /
## reroute / ack effects, the status line and every camera beat read `Kinetic` (state,
## active_span, signals_in) or the resolved spans file. Illustrative: camera easing, label
## fades and the decay envelopes of one-shot effects. Everything is a pure function of
## `Kinetic.tick` (never of accumulated frame time). Rationale and colour roles: ../../LOOKDEV.md.

const Geo := preload("lib/geo.gd")
const Mark := preload("lib/mark.gd")

# ---------------------------------------------------------------- style (one place)
# Colour ROLES. Every entry can be overridden from the job's style as `color.<role>`.
const STYLE := {
	"ground": "#171512",          # deep graphite, faint warm tint
	"backdrop": "#0b0a09",
	"enclosure": "#2a2c30",       # dark anodised metal, low saturation
	"enclosure_trim": "#3b3d41",  # bezel and handles (brighter metal)
	"enclosure_panel": "#1c1d20",
	"in_flight": "#7ee3ff",       # data in flight: cool, blooms to a near-white cyan core
	"fault_hot": "#ff7a1a",       # fault at the instant of the cut (amber) ...
	"fault": "#e2452c",           # ... settling to red; always dashed
	"recovered": "#5fd1a8",       # the active route after route.changed, receipts
	"link_idle": "#8d97a3",       # an `up` link that is not the rerouted route
	"standby": "#4d5258",         # dashed, no glow
	"annotation": "#f1ebe0",      # warm off-white text
	"annotation_dim": "#bdb5a8",  # leaders, secondary text
	"badge": "#a39b8e",           # muted chrome
	"key_light": "#ffe4c4",
	"rim_light": "#8fb3ff",
}
const FONT_SANS := ["Noto Sans"]
const FONT_MONO := ["Liberation Mono", "DejaVu Sans Mono"]
# Type scale in px at 1080 px frame height (landscape). 9:16 multiplies by TALL_TEXT so cap
# heights meet section 12 (28 px at 1080p, 40 px at 1080x1920).
const TYPE := {"label": 40, "status": 44, "chrome": 40}
const TALL_TEXT := 1.43
const MARGIN := 64.0  # px at 1080, safe margin for all chrome

# Illustrative envelopes, in ticks (documented in LOOKDEV.md; they encode no quantity).
const FAULT_FLASH_TICKS := 70.0
const FAULT_COOL_TICKS := 260.0   # amber -> red on the failed link
const LOSS_DISSOLVE_TICKS := 240.0
const RING_TICKS := 520.0
const RECEIPT_TICKS := 220.0
const LABEL_FADE_TICKS := 60.0
const TRAIL_TICKS := 150  # trail = distance travelled over the last 150 ticks of the trace

# ---------------------------------------------------------------- geometry
const NODE_X := 4.4
const BODY := Vector3(1.8, 1.9, 1.4)  # cabinet body (w, h, d)
const KICK_H := 0.1
const CHAMFER := 0.035
const PORT_OUT := 0.16  # socket plate + collar protrude this far from the inner face
const CABLE_X := NODE_X - BODY.x * 0.5 - PORT_OUT
const CABLE_R := 0.062
const LINKS := {  # y at the ports, z, sag at mid-span
	"link:P": [0.5, 0.28, 0.13],
	"link:S": [1.46, -0.28, 0.2],
}
const STAND_X := [-1.7, 1.7]

const CONDUIT_SHADER := """
shader_type spatial;
render_mode cull_back;
uniform vec3 sheath_col : source_color = vec3(0.05);
uniform float length_units = 6.8;
uniform float stripe_ang = 0.85;     // status stripe: radians from up toward +z
uniform vec3 line_col : source_color = vec3(1.0);
uniform float line_energy = 0.0;
uniform float stripe_w = 0.12;       // half width of the status stripe, radians
uniform float dash = 0.0;            // 1 = dashed stripe (standby / fault: shape, not only hue)
uniform vec3 flow_col : source_color = vec3(1.0);
uniform float flow_a = -1.0;         // lit core band [flow_a, flow_b] along the conduit, 0 = A end
uniform float flow_b = -1.0;
uniform float flow_head = -1.0;
uniform float flow_energy = 0.0;
uniform vec3 break_col : source_color = vec3(1.0);
uniform float break_u = -1.0;        // where the lost packet's data stopped
uniform float break_energy = 0.0;
uniform vec3 pulse_col : source_color = vec3(1.0);
uniform float pulse_energy = 0.0;
varying vec2 uvv;
void vertex() { uvv = UV; }
void fragment() {
	float s = uvv.x;
	float ang = uvv.y * 6.2831853;
	float m = s * length_units;                       // metres along the cable
	// braided jacket: a slow helical rib in albedo and roughness, no emission
	float rib = sin((m * 22.0 + uvv.y * 3.0) * 6.2831853) * 0.5 + 0.5;
	ALBEDO = sheath_col * (0.94 + 0.1 * rib);
	ROUGHNESS = 0.42 + 0.1 * rib;
	METALLIC = 0.0;
	SPECULAR = 0.45;
	// view-facing centre of the cable: where an inner core shows through the jacket
	float core = pow(clamp(dot(NORMAL, VIEW), 0.0, 1.0), 2.2);
	float d = abs(mod(ang - stripe_ang + 3.14159265, 6.2831853) - 3.14159265);
	float stripe = 1.0 - smoothstep(stripe_w, stripe_w + 0.07, d);
	float dashes = mix(1.0, step(0.55, fract(m * 1.7)), dash);
	float lo = min(flow_a, flow_b);
	float hi = max(flow_a, flow_b);
	float band = smoothstep(lo - 0.003, lo + 0.003, s) * (1.0 - smoothstep(hi - 0.003, hi + 0.003, s));
	float span = max(hi - lo, 0.0001);
	float ramp = 1.0 - clamp(abs(s - flow_head) / span, 0.0, 1.0);
	float brk = exp(-pow((s - break_u) * length_units / 0.09, 2.0));
	vec3 e = line_col * line_energy * stripe * dashes;
	e += flow_col * flow_energy * band * (0.15 + 0.85 * ramp * ramp) * (0.2 + 0.8 * core);
	e += break_col * break_energy * brk * (0.35 + 0.65 * core);
	e += pulse_col * pulse_energy * stripe * dashes;
	EMISSION = e;
}
"""

const GROUND_SHADER := """
shader_type spatial;
uniform vec3 base : source_color = vec3(0.1);
uniform float radius = 16.0;
varying vec3 wpos;
float hash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
float vnoise(vec2 p) {
	vec2 i = floor(p); vec2 f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
	return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
}
void vertex() { wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	float r = length(wpos.xz * vec2(0.62, 1.0));
	float k = 1.0 - smoothstep(radius * 0.25, radius, r);
	float n = vnoise(wpos.xz * 0.9) * 0.55 + vnoise(wpos.xz * 6.0) * 0.3 + vnoise(wpos.xz * 37.0) * 0.15;
	ALBEDO = base * k * (0.88 + 0.24 * n);
	ROUGHNESS = 0.58 + 0.26 * n;
	SPECULAR = 0.42 * k;
}
"""

const PACKET_SHADER := """
shader_type spatial;
uniform vec3 albedo_col : source_color = vec3(0.5);
uniform vec3 emit_col : source_color = vec3(1.0);
uniform float emit_energy = 1.0;
uniform float dissolve = 0.0;   // 0..1: share of cells discarded (illustrative fade of a lost packet)
varying vec3 opos;
float hash3(vec3 p) { return fract(sin(dot(p, vec3(12.9898, 78.233, 37.719))) * 43758.5453); }
void vertex() { opos = VERTEX; }
void fragment() {
	if (dissolve > 0.0 && hash3(floor(opos * 42.0)) < dissolve) { discard; }
	float facing = clamp(dot(NORMAL, VIEW), 0.0, 1.0);
	ALBEDO = albedo_col;
	ROUGHNESS = 0.3;
	EMISSION = emit_col * emit_energy * (0.55 + 0.45 * facing);
}
"""

const VIGNETTE_SHADER := """
shader_type canvas_item;
uniform float aspect = 1.7778;
uniform float strength = 0.5;
void fragment() {
	// elliptical, following the frame; slightly rounder on the long axis
	vec2 p = (UV - 0.5) * 2.0;
	p *= aspect >= 1.0 ? vec2(0.92, 1.0) : vec2(1.0, 0.92);
	float r = length(p);
	COLOR = vec4(0.0, 0.0, 0.0, strength * smoothstep(0.55, 1.25, r));
}
"""

var col := {}
var spans := {}  # entity -> resolved span dictionaries from spans.jsonl
var beats := {}  # trace ticks that key the camera and labels (read once at _ready)
var ready_done := false
var tall := false

var path := {}  # link -> [points PackedVector3Array, cumulative lengths PackedFloat32Array]
var conduit := {}  # link -> MeshInstance3D
var cmat := {}  # link -> ShaderMaterial
var node_root := {}  # "A"/"B" -> Node3D
var strip_mat := {}  # "A"/"B" -> StandardMaterial3D
var pkt := {}  # entity -> MeshInstance3D
var pkt_mat := {}  # entity -> ShaderMaterial
var ring: MeshInstance3D
var ring_mat: StandardMaterial3D

var ts := 1.0  # text scale
var ls := 1.0  # layout scale (px at 1080 on the short side)
var lw := 1.0  # hairline width
var font_sans: Font
var font_mono: Font
var lbl := {}
var leaders := {}
var dots := {}
var status_lbl := {}
var status_mark: Control
var badge: Control
var timescale_lbl: Label


func _ready() -> void:
	for role in STYLE:
		col[role] = Color(str(Kinetic.style("color." + role, STYLE[role])))
	var vp := get_viewport().get_visible_rect().size
	tall = vp.y > vp.x
	_load_spans()
	_read_beats()
	_build_paths()
	_build_environment()
	_build_stage()
	for side in ["A", "B"]:
		_build_node(side)
	_build_conduits()
	_build_packets()
	_build_ui()
	# Gentle far depth of field: softens only the backdrop (no history, no temporal effect).
	for cam_name in ["hero", "tall"]:
		var cam := get_node_or_null(cam_name) as Camera3D
		if cam != null:
			var ca := CameraAttributesPractical.new()
			ca.dof_blur_far_enabled = true
			ca.dof_blur_far_distance = 16.0
			ca.dof_blur_far_transition = 8.0
			ca.dof_blur_amount = 0.06
			cam.attributes = ca
	Kinetic.track("node:A", node_root["A"])
	Kinetic.track("node:B", node_root["B"])
	for link in conduit:
		Kinetic.track(link, conduit[link])
	for e in pkt:
		Kinetic.track(e, pkt[e])
	ready_done = true
	_update()


func _process(_delta: float) -> void:
	_update()


# ---------------------------------------------------------------- trace access


func _load_spans() -> void:
	# Resolved spans give each transit's begin/end/outcome (needed to place a packet lost
	# mid-flight, see _packet_u, and to key camera beats). Read-only; same file the runtime samples.
	var dir := str(Kinetic.job.get("trace_dir", ""))
	if dir == "":
		return
	var f := FileAccess.open(dir.path_join("spans.jsonl"), FileAccess.READ)
	if f == null:
		return
	while not f.eof_reached():
		var line := f.get_line().strip_edges()
		if line == "":
			continue
		var s = JSON.parse_string(line)
		if typeof(s) == TYPE_DICTIONARY:
			spans[str(s["entity"])] = s


func _read_beats() -> void:
	# Every camera beat is keyed to a tick that is in the trace: the four signals and the
	# begin/end of each transit span. Read once; the camera is then a pure function of the tick.
	var end_tick := int(Kinetic.job.get("tick_end", 0))
	for name in ["fault.injected", "timeout.fired", "route.changed", "ack.received"]:
		beats[name] = -1
	beats["route.via"] = ""
	for s in Kinetic.signals_in(-1, end_tick):
		var n := str(s.get("name", ""))
		if beats.has(n) and int(beats[n]) < 0:
			beats[n] = int(s.get("tick", -1))
			if n == "route.changed":
				beats["route.via"] = str(s.get("attrs", {}).get("via", ""))
	for e in ["packet:1", "packet:2", "packet:ack2"]:
		beats[e + ".begin"] = int(spans[e]["begin_tick"]) if spans.has(e) else -1
		beats[e + ".end"] = int(spans[e]["end_tick"]) if spans.has(e) else -1
	beats["end"] = end_tick


func _sig_on(sig_name: String, t: int) -> int:
	# Tick of `sig_name` if it has fired by tick t, else -1 (state shown only once it happened).
	var k := int(beats.get(sig_name, -1))
	return k if k >= 0 and k <= t else -1


func _status(entity: String) -> String:
	return str(Kinetic.state(entity).get("status", ""))


func _packet_u(entity: String, t: int) -> float:
	# Position along the link (0 = sender end): the span's PLANNED progress (contract "Planned
	# end"). At the current tick it comes straight from the runtime: active_span's
	# planned_progress while in flight, last_span's planned_progress (where it stopped) after
	# the span ended. For an earlier tick (the trail tail) the same contract formula is
	# evaluated on the resolved span: (t - begin) / (planned_end - begin), t clamped to the span.
	if not spans.has(entity):
		return -1.0
	if t == Kinetic.tick:
		var a := Kinetic.active_span(entity)
		if not a.is_empty():
			return float(a["planned_progress"])
		var l := Kinetic.last_span(entity)
		if not l.is_empty():
			return float(l["planned_progress"])
	var s: Dictionary = spans[entity]
	var b := int(s["begin_tick"])
	var e := int(s["end_tick"])
	var pe := int(s["planned_end_tick"]) if s.get("planned_end_tick") != null else e
	t = clampi(t, b, e)
	return clampf(float(t - b) / maxf(float(pe - b), 1.0), 0.0, 1.0)


func _span_dir_from_b(entity: String) -> bool:
	return spans.has(entity) and str(spans[entity]["attrs"].get("from", "")) == "node:B"


func _span_via(entity: String) -> String:
	return str(spans[entity]["attrs"].get("via", "")) if spans.has(entity) else ""


func _pos_u(entity: String, t: int) -> float:
	# Position along the link measured from A (0) to B (1).
	var u := _packet_u(entity, t)
	return 1.0 - u if _span_dir_from_b(entity) else u


# ---------------------------------------------------------------- cable paths


func _build_paths() -> void:
	# Each cable hangs between its two sockets with a gentle sag. Packets are placed by arc
	# length, so linear span progress stays linear along the visible cable.
	for link in LINKS:
		var y: float = LINKS[link][0]
		var z: float = LINKS[link][1]
		var sag: float = LINKS[link][2]
		var pts := PackedVector3Array()
		var n := 160
		for i in n + 1:
			var u := float(i) / float(n)
			var x := lerpf(-CABLE_X, CABLE_X, u)
			pts.append(Vector3(x, y - sag * 4.0 * u * (1.0 - u), z))
		var cum := PackedFloat32Array([0.0])
		for i in range(1, pts.size()):
			cum.append(cum[i - 1] + pts[i].distance_to(pts[i - 1]))
		path[link] = [pts, cum]


func _link_point(link: String, u: float) -> Vector3:
	var pts: PackedVector3Array = path[link][0]
	var cum: PackedFloat32Array = path[link][1]
	var target := clampf(u, 0.0, 1.0) * cum[cum.size() - 1]
	var i := cum.bsearch(target)
	if i <= 0:
		return pts[0]
	if i >= pts.size():
		return pts[pts.size() - 1]
	var w := (target - cum[i - 1]) / maxf(cum[i] - cum[i - 1], 1e-6)
	return pts[i - 1].lerp(pts[i], w)


func _link_tangent(link: String, u: float) -> Vector3:
	return (_link_point(link, u + 0.004) - _link_point(link, u - 0.004)).normalized()


func _link_len(link: String) -> float:
	var cum: PackedFloat32Array = path[link][1]
	return cum[cum.size() - 1]


# ---------------------------------------------------------------- build


func _build_environment() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = col["backdrop"]
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color("#8a8f99")
	env.ambient_light_energy = 0.03
	env.tonemap_mode = Environment.TONE_MAPPER_AGX
	env.tonemap_exposure = 1.05
	env.tonemap_agx_white = 12.0
	env.tonemap_agx_contrast = 1.15
	env.ssao_enabled = true
	env.ssao_radius = 0.7
	env.ssao_intensity = 2.2
	env.ssao_power = 1.5
	env.glow_enabled = true
	env.glow_normalized = true
	env.glow_intensity = 0.8
	env.glow_strength = 1.0
	env.glow_bloom = 0.0
	env.glow_hdr_threshold = 1.2
	env.glow_blend_mode = Environment.GLOW_BLEND_MODE_SCREEN
	for i in 7:
		env.set_glow_level(i, [0.0, 0.0, 1.0, 0.8, 0.5, 0.25, 0.0][i])
	# Depth fog toward the warm-black backdrop: separates the far cabinet without volumetrics.
	env.fog_enabled = true
	env.fog_light_color = Color("#0e0d0c")
	env.fog_density = 0.018
	env.fog_sky_affect = 0.0
	env.adjustment_enabled = true
	env.adjustment_saturation = 0.92
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)

	# Key: one high, motivated warm-neutral lab lamp, front-left; its pool defines the stage.
	var key := SpotLight3D.new()
	key.light_color = col["key_light"]
	key.light_energy = 170.0
	key.spot_range = 32.0
	key.spot_angle = 30.0
	key.spot_angle_attenuation = 2.2
	key.spot_attenuation = 1.0
	key.shadow_enabled = true
	key.shadow_blur = 2.0
	key.light_specular = 0.8
	add_child(key)
	var kp := Vector3(-5.5, 12.0, 8.0)
	key.global_transform = Transform3D(Basis.looking_at(Vector3(0.4, 0.4, -0.6) - kp, Vector3.UP), kp)
	# Rim: cool, from behind and high, traces the chamfers and cable tops.
	# One narrow spot per cabinet, so the floor in front is not washed cool.
	for x in [-NODE_X, NODE_X]:
		var rim := SpotLight3D.new()
		rim.light_color = col["rim_light"]
		rim.light_energy = 28.0
		rim.spot_range = 16.0
		rim.spot_angle = 22.0
		rim.spot_angle_attenuation = 1.5
		rim.shadow_enabled = false
		add_child(rim)
		var rp := Vector3(x * 0.6, 6.5, -7.5)
		rim.global_transform = Transform3D(Basis.looking_at(Vector3(x, 1.4, 0.0) - rp, Vector3.UP), rp)
	# Fill: very low, neutral, from the camera side so the cabinet fronts never go to black.
	var fill := OmniLight3D.new()
	fill.light_color = Color("#d8d2c8")
	fill.light_energy = 0.14
	fill.omni_range = 14.0
	fill.shadow_enabled = false
	fill.position = Vector3(1.5, 2.6, 9.0)
	add_child(fill)
	# Backdrop wash: a dim cool spot on a far wall gives depth behind the cabinets.
	var wash := SpotLight3D.new()
	wash.light_color = Color("#6f86a8")
	wash.light_energy = 6.0
	wash.spot_range = 26.0
	wash.spot_angle = 30.0
	wash.spot_angle_attenuation = 2.0
	wash.shadow_enabled = false
	add_child(wash)
	var wp := Vector3(0.0, 9.0, 2.0)
	wash.global_transform = Transform3D(Basis.looking_at(Vector3(0.0, 2.0, -9.0) - wp, Vector3.UP), wp)


func _std(albedo: Color, rough: float, metal: float) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = albedo
	m.roughness = rough
	m.metallic = metal
	return m


func _cbox(size: Vector3, c: float, pos: Vector3, mat: Material, parent: Node3D) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	mi.mesh = Geo.chamfer_box(size, c)
	mi.material_override = mat
	mi.position = pos
	parent.add_child(mi)
	return mi


func _cyl(r0: float, r1: float, h: float, pos: Vector3, rot: Vector3, mat: Material, parent: Node3D) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	cm.top_radius = r0
	cm.bottom_radius = r1
	cm.height = h
	cm.radial_segments = 32
	cm.rings = 1
	mi.mesh = cm
	mi.material_override = mat
	mi.position = pos
	mi.rotation = rot
	parent.add_child(mi)
	return mi


func _build_stage() -> void:
	var ground := MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(90, 90)
	ground.mesh = pm
	var gsh := Shader.new()
	gsh.code = GROUND_SHADER
	var gm := ShaderMaterial.new()
	gm.shader = gsh
	gm.set_shader_parameter("base", col["ground"])
	ground.material_override = gm
	add_child(ground)
	var wall := MeshInstance3D.new()
	var wm := PlaneMesh.new()
	wm.size = Vector2(60, 20)
	wall.mesh = wm
	wall.material_override = _std(Color("#1e1d1c"), 0.95, 0.0)
	wall.rotation = Vector3(PI * 0.5, 0, 0)
	wall.position = Vector3(0, 9.5, -12.0)
	add_child(wall)


func _build_node(side: String) -> void:
	var sx := -1.0 if side == "A" else 1.0
	var root := Node3D.new()
	root.position = Vector3(sx * NODE_X, 0, 0)
	add_child(root)
	node_root[side] = root
	var body_m := _std(col["enclosure"], 0.48, 0.35)
	var trim_m := _std(col["enclosure_trim"], 0.36, 0.65)
	var panel_m := _std(col["enclosure_panel"], 0.6, 0.2)
	var seam_m := _std(Color("#060607"), 0.9, 0.0)
	seam_m.metallic_specular = 0.1
	var kick_m := _std(Color("#0f1012"), 0.8, 0.1)
	var w := BODY.x
	var h := BODY.y
	var d := BODY.z
	var y0 := KICK_H
	var front := d * 0.5
	# Recessed kick base and the chamfered body.
	_cbox(Vector3(w - 0.16, KICK_H + 0.02, d - 0.16), 0.01, Vector3(0, (KICK_H + 0.02) * 0.5 - 0.01, 0), kick_m, root)
	_cbox(Vector3(w, h, d), CHAMFER, Vector3(0, y0 + h * 0.5, 0), body_m, root)
	# Lid seam: a slightly inset cap reads as a separate top panel.
	_cbox(Vector3(w - 0.1, 0.012, d - 0.1), 0.004, Vector3(0, y0 + h + 0.004, 0), seam_m, root)
	_cbox(Vector3(w - 0.14, 0.03, d - 0.14), 0.01, Vector3(0, y0 + h + 0.02, 0), body_m, root)
	# Front: a protruding bezel frame around a dark inset panel (reads as recessed).
	var bz := 0.07  # bezel bar width
	var fw := w - 0.16
	var fh := h - 0.2
	var fy := y0 + h * 0.5 - 0.02
	var fz := front + 0.012
	_cbox(Vector3(fw, bz, 0.035), 0.012, Vector3(0, fy + fh * 0.5 - bz * 0.5, fz), trim_m, root)
	_cbox(Vector3(fw, bz, 0.035), 0.012, Vector3(0, fy - fh * 0.5 + bz * 0.5, fz), trim_m, root)
	_cbox(Vector3(bz, fh, 0.035), 0.012, Vector3(-fw * 0.5 + bz * 0.5, fy, fz), trim_m, root)
	_cbox(Vector3(bz, fh, 0.035), 0.012, Vector3(fw * 0.5 - bz * 0.5, fy, fz), trim_m, root)
	var pw := fw - bz * 2.0
	var ph := fh - bz * 2.0
	_cbox(Vector3(pw, ph, 0.01), 0.003, Vector3(0, fy, front + 0.002), panel_m, root)
	# Status strip: a dark glass slot near the top of the panel with an emissive bar inside.
	var slot_y := fy + ph * 0.5 - 0.13
	_cbox(Vector3(pw - 0.24, 0.085, 0.012), 0.004, Vector3(0, slot_y, front + 0.008), seam_m, root)
	var strip := StandardMaterial3D.new()
	strip.albedo_color = Color(0.03, 0.03, 0.03)
	strip.emission_enabled = true
	strip.emission = col["annotation"]
	strip.emission_energy_multiplier = 0.9
	strip.roughness = 0.25
	_cbox(Vector3(pw - 0.32, 0.03, 0.01), 0.003, Vector3(0, slot_y, front + 0.012), strip, root)
	strip_mat[side] = strip
	# Three bays below the strip, each a row of chamfered sleds separated by dark seams, as on
	# a rack chassis. No emission: they carry no information.
	var bay_top := slot_y - 0.1
	var bay_bot := fy - ph * 0.5 + 0.03
	var bays := 3
	var sleds := 6
	var bay_h := (bay_top - bay_bot) / float(bays)
	var sled_w := (pw - 0.24) / float(sleds)
	var sled_m := _std(col["enclosure"].lightened(0.04), 0.5, 0.4)
	for bi in bays:
		var cy := bay_top - bay_h * (float(bi) + 0.5)
		_cbox(Vector3(pw - 0.2, bay_h - 0.02, 0.006), 0.002, Vector3(0, cy, front + 0.006), seam_m, root)
		for si in sleds:
			var cx := -(pw - 0.24) * 0.5 + sled_w * (float(si) + 0.5)
			_cbox(Vector3(sled_w - 0.018, bay_h - 0.05, 0.022), 0.006, Vector3(cx, cy, front + 0.014), sled_m, root)
	# Handles at both sides of the panel, on short standoffs.
	for hx in [-pw * 0.5 + 0.09, pw * 0.5 - 0.09]:
		_cbox(Vector3(0.035, 0.46, 0.035), 0.01, Vector3(hx, fy - 0.12, front + 0.06), trim_m, root)
		for hy in [0.17, -0.17]:
			_cbox(Vector3(0.025, 0.025, 0.05), 0.006, Vector3(hx, fy - 0.12 + hy, front + 0.03), trim_m, root)
	# Side seam on both side faces.
	for fx in [-1.0, 1.0]:
		_cbox(Vector3(0.008, h - 0.16, 0.008), 0.002, Vector3(fx * (w * 0.5 + 0.002), y0 + h * 0.5, -0.05), seam_m, root)
	# Ports: where each conduit attaches on the inner face.
	var inner := -sx  # the inner face points toward the other node
	var plate_m := _std(Color("#303236"), 0.38, 0.6)
	var collar_m := _std(Color("#4a4c50"), 0.3, 0.85)
	var hole_m := _std(Color("#030303"), 0.95, 0.0)
	for link in LINKS:
		var py: float = LINKS[link][0]
		var pz: float = LINKS[link][1]
		var face := inner * w * 0.5
		_cbox(Vector3(0.03, 0.34, 0.34), 0.008, Vector3(face + inner * 0.015, py, pz), plate_m, root)
		for qy in [-0.12, 0.12]:
			for qz in [-0.12, 0.12]:
				_cyl(0.013, 0.013, 0.012, Vector3(face + inner * 0.032, py + qy, pz + qz), Vector3(0, 0, PI * 0.5), collar_m, root)
		_cyl(0.105, 0.105, 0.12, Vector3(face + inner * 0.09, py, pz), Vector3(0, 0, PI * 0.5), collar_m, root)
		_cyl(0.085, 0.085, 0.004, Vector3(face + inner * 0.151, py, pz), Vector3(0, 0, PI * 0.5), hole_m, root)
	if side == "A":
		# Timeout ring: lies on the floor around A's footprint.
		ring = MeshInstance3D.new()
		var tm := TorusMesh.new()
		tm.inner_radius = 1.5
		tm.outer_radius = 1.53
		tm.rings = 128
		tm.ring_segments = 6
		ring.mesh = tm
		ring_mat = StandardMaterial3D.new()
		ring_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
		ring_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
		ring_mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
		ring_mat.albedo_color = Color(col["annotation"], 0.0)
		ring.material_override = ring_mat
		ring.position = Vector3(-NODE_X, 0.012, 0)
		ring.visible = false
		add_child(ring)


func _build_conduits() -> void:
	var sh := Shader.new()
	sh.code = CONDUIT_SHADER
	var stand_m := _std(Color("#1d1e21"), 0.55, 0.45)
	var boot_m := _std(Color("#151618"), 0.7, 0.0)
	for link in LINKS:
		var pts: PackedVector3Array = path[link][0]
		var mi := MeshInstance3D.new()
		mi.mesh = Geo.tube(pts, CABLE_R, 28)
		var m := ShaderMaterial.new()
		m.shader = sh
		m.set_shader_parameter("sheath_col", Color("#141516"))
		m.set_shader_parameter("length_units", _link_len(link))
		mi.material_override = m
		add_child(mi)
		conduit[link] = mi
		cmat[link] = m
		# Strain-relief boots where the cable leaves each socket.
		for u in [0.0, 1.0]:
			var p := _link_point(link, u)
			var dirx := 1.0 if u == 0.0 else -1.0
			_cyl(CABLE_R * 1.15, CABLE_R * 1.45, 0.16, p + Vector3(dirx * 0.08, 0, 0), Vector3(0, 0, -dirx * PI * 0.5), boot_m, self)
		# Slim cable stands with a cradle (physical plausibility; no meaning).
		for x in STAND_X:
			var u: float = (x + CABLE_X) / (2.0 * CABLE_X)
			var cy := _link_point(link, u).y - CABLE_R - 0.012
			var z: float = LINKS[link][1]
			_cbox(Vector3(0.035, cy, 0.035), 0.008, Vector3(x, cy * 0.5, z), stand_m, self)
			_cbox(Vector3(0.22, 0.012, 0.18), 0.004, Vector3(x, 0.006, z), stand_m, self)
			_cbox(Vector3(0.06, 0.024, 0.16), 0.006, Vector3(x, cy - 0.006, z), stand_m, self)


func _build_packets() -> void:
	var sh := Shader.new()
	sh.code = PACKET_SHADER
	for e in ["packet:1", "packet:2", "packet:ack2"]:
		var mi := MeshInstance3D.new()
		if e == "packet:ack2":
			var sp := SphereMesh.new()  # the ack is a different shape, not only a size
			sp.radius = 0.085
			sp.height = 0.17
			mi.mesh = sp
		else:
			var cp := CapsuleMesh.new()
			cp.radius = 0.095
			cp.height = 0.44
			mi.mesh = cp
		var m := ShaderMaterial.new()
		m.shader = sh
		mi.material_override = m
		mi.visible = false
		add_child(mi)
		pkt[e] = mi
		pkt_mat[e] = m


# ---------------------------------------------------------------- UI


func _font(names: Array, spacing: float) -> Font:
	var sf := SystemFont.new()
	sf.font_names = PackedStringArray(names)
	sf.antialiasing = TextServer.FONT_ANTIALIASING_GRAY
	sf.hinting = TextServer.HINTING_LIGHT
	sf.subpixel_positioning = TextServer.SUBPIXEL_POSITIONING_AUTO
	var fv := FontVariation.new()
	fv.base_font = sf
	fv.spacing_glyph = int(round(spacing))
	return fv


func _settings(font: Font, px: float, c: Color) -> LabelSettings:
	var s := LabelSettings.new()
	s.font = font
	s.font_size = int(round(px))
	s.font_color = c
	s.shadow_color = Color(0, 0, 0, 0.6)
	s.shadow_size = maxi(1, int(round(6 * ls)))
	s.shadow_offset = Vector2(0, 2 * ls)
	return s


func _new_label(layer: Node, text: String, settings: LabelSettings) -> Label:
	var l := Label.new()
	l.text = text
	l.label_settings = settings
	l.visible = false
	layer.add_child(l)
	Kinetic.label(text, l)
	return l


func _build_ui() -> void:
	var vp := get_viewport().get_visible_rect().size
	ls = minf(vp.x, vp.y) / 1080.0
	ts = TALL_TEXT * vp.x / 1080.0 if tall else vp.y / 1080.0
	lw = maxf(1.0, round(vp.y / 1080.0))
	font_sans = _font(FONT_SANS, 0.6 * ts)
	font_mono = _font(FONT_MONO, 0.0)
	# Lens vignette under the HUD (static; a look choice, carries nothing).
	var vlayer := CanvasLayer.new()
	vlayer.layer = 0
	add_child(vlayer)
	var vig := ColorRect.new()
	vig.size = vp
	vig.mouse_filter = Control.MOUSE_FILTER_IGNORE
	var vsh := Shader.new()
	vsh.code = VIGNETTE_SHADER
	var vm := ShaderMaterial.new()
	vm.shader = vsh
	vm.set_shader_parameter("aspect", vp.x / vp.y)
	vig.material = vm
	vlayer.add_child(vig)
	var layer := CanvasLayer.new()
	layer.layer = 1
	add_child(layer)

	var role_a := str(Kinetic.state("node:A").get("role", ""))
	var role_b := str(Kinetic.state("node:B").get("role", ""))
	var texts := {
		"A": "A · " + role_a if role_a != "" else "A",
		"B": "B · " + role_b if role_b != "" else "B",
		"primary": "primary",
		"secondary": "secondary",
	}
	for key in texts:
		var ln := Line2D.new()
		ln.width = lw
		ln.default_color = Color(col["annotation_dim"], 0.85)
		ln.antialiased = false
		ln.joint_mode = Line2D.LINE_JOINT_SHARP
		ln.visible = false
		layer.add_child(ln)
		leaders[key] = ln
		var dot := Mark.new()
		dot.size = Vector2(9, 9) * ls * (1.3 if tall else 1.0)
		dot.set_mark("dot", col["annotation"], lw)
		dot.visible = false
		layer.add_child(dot)
		dots[key] = dot
		var big: bool = key in ["A", "B"]
		lbl[key] = _new_label(layer, texts[key], _settings(font_sans, TYPE["label"] * ts, col["annotation"] if big else col["annotation_dim"]))

	var stexts := {
		"cut": "primary link cut · packet lost",
		"reroute": "timeout · resend on secondary",
		"delivered": "resend delivered · acknowledged",
	}
	# "acknowledged" is only shown once ack.received has fired; see _update_labels.
	stexts["delivered_noack"] = "resend delivered"
	for k in stexts:
		status_lbl[k] = _new_label(layer, stexts[k], _settings(font_sans, TYPE["status"] * ts, col["annotation"]))
	status_mark = Mark.new()
	status_mark.size = Vector2(26, 26) * ls * (1.35 if tall else 1.0)
	status_mark.visible = false
	layer.add_child(status_mark)

	# Evidence badge: fixed top-left corner, mono, hairline frame, muted.
	var margin := MARGIN * ls
	var top := (0.14 * vp.y + 16.0 * ls) if tall else margin
	var badge_text := _badge_text()
	var panel := PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.03, 0.03, 0.03, 0.45)
	sb.border_color = Color(col["badge"], 0.7)
	sb.set_border_width_all(int(lw))
	sb.set_corner_radius_all(0)
	sb.content_margin_left = 16 * ls
	sb.content_margin_right = 18 * ls
	sb.content_margin_top = 6 * ls
	sb.content_margin_bottom = 8 * ls
	panel.add_theme_stylebox_override("panel", sb)
	panel.position = Vector2(margin, top)
	layer.add_child(panel)
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", int(-4 * ls))
	panel.add_child(box)
	var parts: Array = Array(badge_text.split(" · "))
	for i in parts.size():
		var bl := Label.new()
		bl.text = str(parts[i])
		var bs := _settings(font_mono, TYPE["chrome"] * ts, col["annotation"] if i == 0 else col["badge"])
		bs.shadow_size = 0
		bl.label_settings = bs
		box.add_child(bl)
	panel.visible = badge_text != ""
	badge = panel
	if badge_text != "":
		Kinetic.label(badge_text, panel)

	timescale_lbl = Label.new()
	timescale_lbl.text = _timescale_text()
	var tss := _settings(font_mono, TYPE["chrome"] * ts, col["badge"])
	tss.shadow_size = 0
	timescale_lbl.label_settings = tss
	layer.add_child(timescale_lbl)
	timescale_lbl.visible = timescale_lbl.text != ""
	var tsz := timescale_lbl.get_minimum_size()
	if tall:
		timescale_lbl.position = Vector2(margin + 16 * ls, top + panel.get_combined_minimum_size().y + 10 * ls)
	else:
		timescale_lbl.position = Vector2(vp.x - margin - tsz.x, margin + 6 * ls)
	if timescale_lbl.text != "":
		Kinetic.label(timescale_lbl.text, timescale_lbl)


# Badge per docs/evidence-policy.md: the evidence class from the job, plus this scene's
# qualifier (what the model is not). Classes without a badge return "".
const BADGE_PREFIX := {
	"simulated": "SIMULATED",
	"measured": "MEASURED",
	"sourced": "LIST PRICE",
	"hypothetical": "DESIGN TARGET",
}
const BADGE_QUALIFIER := {
	"simulated": "model, not a real network",
	"hypothetical": "not measured",
}


func _badge_text() -> String:
	var ev := str(Kinetic.job.get("evidence", "simulated"))
	if not BADGE_PREFIX.has(ev):
		return ""
	var q := str(Kinetic.style("badge_qualifier", BADGE_QUALIFIER.get(ev, "")))
	return BADGE_PREFIX[ev] + (" · " + q if q != "" else "")


func _timescale_text() -> String:
	# The shot's timemap label from film.yaml (job.time_label); derived only as a fallback.
	var given = Kinetic.job.get("time_label")
	if given != null and str(given) != "":
		return str(given)
	if Kinetic.job.get("tick_end") == null:
		return ""
	var rate := _exact(Kinetic.job.get("tick_rate", "1/1000"))
	var sim_s := float(int(Kinetic.job["tick_end"]) - int(Kinetic.job["tick_start"])) * rate
	var film_s := float(Kinetic.job["frame_count"]) / _exact(Kinetic.job.get("fps", 30))
	var speed := sim_s / film_s
	if absf(speed - 1.0) < 0.005:
		return ""
	if speed < 1.0:
		return "time slowed %.1f×" % (1.0 / speed)
	return "time sped up %.1f×" % speed


func _exact(v) -> float:
	var s := str(v)
	if "/" in s:
		var parts := s.split("/")
		return float(parts[0]) / float(parts[1])
	return float(s)


# ---------------------------------------------------------------- per frame


static func _smooth(a: float, b: float, x: float) -> float:
	# The one illustrative curve family used in this scene (smootherstep).
	if b <= a:
		return 1.0 if x >= b else 0.0
	var t := clampf((x - a) / (b - a), 0.0, 1.0)
	return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


func _update() -> void:
	if not ready_done:
		return
	var t: int = Kinetic.tick
	_update_links(t)
	_update_packets(t)
	_update_nodes(t)
	_update_cameras(t)
	_update_labels(t)


func _update_links(t: int) -> void:
	var t_fault := _sig_on("fault.injected", t)
	var t_route := _sig_on("route.changed", t)
	var route_via := str(beats["route.via"]) if t_route >= 0 else ""
	for link in cmat:
		var m: ShaderMaterial = cmat[link]
		var st := _status(link)
		var line_col: Color = col["link_idle"]
		var line_e := 0.0
		var dash := 0.0
		var stripe_w := 0.12  # a hairline stripe on the cable's top-front
		match st:
			"up":
				# The link named by route.changed is the recovered route; any other up link is idle-up.
				if link == route_via:
					line_col = col["recovered"]
					line_e = 0.9
					stripe_w = 0.3
				else:
					line_e = 0.35
			"standby":
				line_col = col["standby"]
				line_e = 0.3
				dash = 1.0
			"failed":
				# Full-circumference dashed bands (hazard marking: shape, not only hue), amber at
				# the instant of the cut, cooling to red (illustrative envelope).
				var k := _smooth(0.0, FAULT_COOL_TICKS, float(t - t_fault)) if t_fault >= 0 else 1.0
				line_col = col["fault_hot"].lerp(col["fault"], k)
				line_e = lerpf(0.5, 0.7, k)
				dash = 1.0
				stripe_w = PI
			"down":
				line_col = col["fault"]
				line_e = 0.4
				dash = 1.0
				stripe_w = PI
		m.set_shader_parameter("stripe_w", stripe_w)
		m.set_shader_parameter("line_col", line_col)
		m.set_shader_parameter("line_energy", line_e)
		m.set_shader_parameter("dash", dash)
		var pulse := 0.0
		if t_fault >= 0 and link == "link:P":
			pulse = 1.0 * exp(-float(t - t_fault) / FAULT_FLASH_TICKS)
		m.set_shader_parameter("pulse_col", col["fault_hot"])
		m.set_shader_parameter("pulse_energy", pulse)
		# Core glow only where a packet is on this link right now; the trail is the distance
		# covered over the last TRAIL_TICKS of the trace.
		var lit := false
		var brk_e := 0.0
		for e in pkt:
			if _span_via(e) != link:
				continue
			if not Kinetic.active_span(e).is_empty():
				var u := _pos_u(e, t)
				var u_tail := _pos_u(e, t - TRAIL_TICKS)
				m.set_shader_parameter("flow_a", u_tail)
				m.set_shader_parameter("flow_b", u)
				m.set_shader_parameter("flow_head", u)
				m.set_shader_parameter("flow_col", col["in_flight"])
				m.set_shader_parameter("flow_energy", 4.0)
				lit = true
			elif _status(e) == "lost":
				# Broken glow: the trail freezes where the data stopped and dies out with the
				# packet; an ember marks the stop point. Envelope is illustrative.
				var end_t := int(spans[e]["end_tick"])
				var k := clampf(float(t - end_t) / LOSS_DISSOLVE_TICKS, 0.0, 1.0)
				var u := _pos_u(e, end_t)
				var u_tail := _pos_u(e, end_t - TRAIL_TICKS)
				m.set_shader_parameter("flow_a", u_tail)
				m.set_shader_parameter("flow_b", u)
				m.set_shader_parameter("flow_head", u)
				m.set_shader_parameter("flow_col", col["in_flight"].lerp(col["fault"], _smooth(0.0, 0.3, k)))
				m.set_shader_parameter("flow_energy", 4.0 * pow(1.0 - k, 2.0))
				m.set_shader_parameter("break_u", u)
				m.set_shader_parameter("break_col", col["fault_hot"].lerp(col["fault"], k))
				brk_e = 5.0 * pow(1.0 - k, 1.5)
				lit = k < 1.0
		m.set_shader_parameter("break_energy", brk_e)
		if not lit:
			m.set_shader_parameter("flow_energy", 0.0)
			m.set_shader_parameter("flow_a", -1.0)
			m.set_shader_parameter("flow_b", -1.0)


func _update_packets(t: int) -> void:
	for e in pkt:
		var mi: MeshInstance3D = pkt[e]
		var m: ShaderMaterial = pkt_mat[e]
		if not Kinetic.alive(e) or not spans.has(e):
			mi.visible = false
			continue
		var via := _span_via(e)
		var u := _pos_u(e, t)
		mi.position = _link_point(via, u)
		var tan := _link_tangent(via, u)
		mi.basis = Basis(Quaternion(Vector3.UP, tan))
		var st := _status(e)
		mi.scale = Vector3.ONE
		m.set_shader_parameter("dissolve", 0.0)
		if st == "in_flight":
			mi.visible = true
			m.set_shader_parameter("albedo_col", col["in_flight"])
			m.set_shader_parameter("emit_col", col["in_flight"])
			m.set_shader_parameter("emit_energy", 6.0)
		elif st == "lost":
			# Stops where it was when the link was cut, turns fault-coloured, then dissolves
			# over LOSS_DISSOLVE_TICKS (illustrative).
			var k := clampf(float(t - int(spans[e]["end_tick"])) / LOSS_DISSOLVE_TICKS, 0.0, 1.0)
			mi.visible = k < 1.0
			var c: Color = col["fault_hot"].lerp(col["fault"], _smooth(0.0, 0.5, k))
			m.set_shader_parameter("albedo_col", c.darkened(0.3))
			m.set_shader_parameter("emit_col", c)
			m.set_shader_parameter("emit_energy", 5.0 * pow(1.0 - k, 1.4))
			m.set_shader_parameter("dissolve", _smooth(0.15, 1.0, k))
		else:
			# Delivered: docked in the receiving socket, barely lit (state shown, not motion).
			mi.visible = true
			m.set_shader_parameter("albedo_col", col["recovered"].darkened(0.55))
			m.set_shader_parameter("emit_col", col["recovered"])
			m.set_shader_parameter("emit_energy", 0.5)


func _update_nodes(t: int) -> void:
	# Receipt pulses on the status strips: B when packet 2 is delivered, A on ack.received.
	var b_pulse := 0.0
	if spans.has("packet:2") and _status("packet:2") == "delivered":
		b_pulse = exp(-float(t - int(spans["packet:2"]["end_tick"])) / RECEIPT_TICKS)
	var a_pulse := 0.0
	var t_ack := _sig_on("ack.received", t)
	if t_ack >= 0:
		a_pulse = exp(-float(t - t_ack) / RECEIPT_TICKS)
	for pair in [["A", a_pulse], ["B", b_pulse]]:
		var sm: StandardMaterial3D = strip_mat[pair[0]]
		var p: float = pair[1]
		sm.emission = col["annotation"].lerp(col["recovered"], minf(1.0, p * 1.6))
		sm.emission_energy_multiplier = 0.9 + 4.5 * p
	# Timeout: a calm ring spreading from A's footprint (envelope illustrative).
	var t_timeout := _sig_on("timeout.fired", t)
	if t_timeout >= 0 and t - t_timeout < RING_TICKS:
		var s := float(t - t_timeout) / RING_TICKS
		ring.visible = true
		var r := 0.95 + 0.7 * _smooth(0.0, 1.0, s)
		ring.scale = Vector3(r, 1.0, r)
		ring_mat.albedo_color = Color(col["annotation"], 0.85 * pow(1.0 - s, 1.6))
	else:
		ring.visible = false


# ---------------------------------------------------------------- camera
# Camera poses: [position, target, fov]. Every beat is keyed to a trace tick (see _read_beats):
#   establish   [0, fault.injected)          slow creep driven by packet 1's span progress
#   consequence [fault.injected -> timeout.fired]  push to a 3/4 on A with the dead cable receding
#   follow      [route.changed, packet:2 end]       dolly driven by packet 2's span progress
#   settle      [packet:2 end -> ack.received]      pull back wide; hold after the ack


func _poses(cam: String) -> Dictionary:
	if cam == "tall":
		return {
			# Looking down the link from A's side: A near and low, B far and high, so the
			# route reads vertically in the 9:16 frame.
			# A longer lens pitched ~18 deg down keeps the set between ~30 % and ~70 % of
			# the frame height: chrome above, status line and caption band below.
			"wide0": [Vector3(-9.5, 3.9, 4.5), Vector3(-1.0, 0.15, 0), 38.0],
			"wide1": [Vector3(-9.0, 3.7, 4.3), Vector3(-0.6, 0.18, 0), 38.0],
			"a": [Vector3(-8.0, 2.6, 3.9), Vector3(-2.2, -0.15, 0), 44.0],
			"track0": [Vector3(-8.8, 3.4, 4.4), Vector3(-1.8, 0.35, 0), 40.0],
			"track1": [Vector3(-4.6, 3.6, 4.6), Vector3(2.6, 0.6, 0), 40.0],
			"end": [Vector3(-9.8, 4.0, 4.7), Vector3(-1.0, 0.15, 0), 38.0],
		}
	return {
		"wide0": [Vector3(-0.5, 2.3, 11.4), Vector3(-0.1, 0.95, 0), 34.0],
		"wide1": [Vector3(0.0, 2.05, 11.0), Vector3(0.05, 0.9, 0), 34.0],
		"a": [Vector3(-8.6, 1.55, 5.2), Vector3(-1.4, 0.85, 0), 40.0],
		"track0": [Vector3(-2.6, 2.4, 7.4), Vector3(-2.0, 1.15, 0), 36.0],
		"track1": [Vector3(2.6, 2.4, 7.4), Vector3(2.0, 1.15, 0), 36.0],
		"end": [Vector3(-0.3, 2.5, 12.2), Vector3(0.0, 0.95, 0), 34.0],
	}


static func _mix(p: Array, q: Array, w: float) -> Array:
	return [p[0].lerp(q[0], w), p[1].lerp(q[1], w), lerpf(p[2], q[2], w)]


func _span_u(entity: String, t: int) -> float:
	# Span progress clamped to [0, 1] at any tick (0 before, 1 after).
	var b := int(beats.get(entity + ".begin", -1))
	var e := int(beats.get(entity + ".end", -1))
	if b < 0 or e <= b:
		return 0.0
	return clampf(float(t - b) / float(e - b), 0.0, 1.0)


func _cam_pose(cam: String, t: int) -> Array:
	var P := _poses(cam)
	var t_fault := int(beats["fault.injected"])
	var t_timeout := int(beats["timeout.fired"])
	var t_route := int(beats["route.changed"])
	var t_ack := int(beats["ack.received"])
	var p2_end := int(beats["packet:2.end"])
	if Kinetic.calm:
		# Reduced motion: three static framings, cut on trace beats. Wide through the loss and
		# the timeout (the lost packet stays in frame), the secondary from route.changed to
		# packet 2's delivery, wide again for the ack.
		if t_route >= 0 and t >= t_route and (p2_end < 0 or t < p2_end):
			return _mix(P["track0"], P["track1"], 0.5)
		if p2_end >= 0 and t >= p2_end:
			return P["end"]
		return P["wide0"]

	# establish: creep in while packet 1 is in flight (its span ends at the fault tick)
	var pose: Array = _mix(P["wide0"], P["wide1"], _smooth(0.0, 1.0, _span_u("packet:1", t)))
	if t_fault >= 0:
		# consequence: leaves at fault.injected, lands as timeout.fired arrives
		var land := t_timeout if t_timeout > t_fault else t_fault + 300
		pose = _mix(pose, P["a"], _smooth(float(t_fault), float(land), float(t)))
	if t_route >= 0:
		# follow: the dolly runs on packet 2's span progress
		var u2 := _span_u("packet:2", t)
		var track := _mix(P["track0"], P["track1"], _smooth(0.0, 1.0, u2))
		pose = _mix(pose, track, _smooth(0.0, 0.2, u2) if t >= t_route else 0.0)
	if p2_end >= 0 and t >= p2_end:
		# settle: from packet 2's delivery to ack.received
		var land2 := t_ack if t_ack > p2_end else p2_end + 600
		pose = _mix(pose, P["end"], _smooth(float(p2_end), float(land2), float(t)))
	return pose


func _update_cameras(t: int) -> void:
	for cam_name in ["hero", "tall"]:
		var cam := get_node_or_null(cam_name) as Camera3D
		if cam == null:
			continue
		var p := _cam_pose(cam_name, t)
		cam.global_transform = Transform3D(Basis.looking_at(p[1] - p[0], Vector3.UP), p[0])
		cam.fov = p[2]


# ---------------------------------------------------------------- labels


func _fade(t: int, t_on: int) -> float:
	# Labels fade in over LABEL_FADE_TICKS; they cut out (no cross-fades, so the count never exceeds 4).
	if t_on < 0 or t < t_on:
		return 0.0
	return clampf(float(t - t_on) / LABEL_FADE_TICKS, 0.0, 1.0)


func _update_labels(t: int) -> void:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return
	var vp := get_viewport().get_visible_rect().size
	var t_fault := _sig_on("fault.injected", t)
	var t_timeout := _sig_on("timeout.fired", t)
	var t_ack := _sig_on("ack.received", t)
	var p2_active := not Kinetic.active_span("packet:2").is_empty()
	var u2 := _packet_u("packet:2", t) if p2_active else 0.0
	var delivered := _status("packet:2") == "delivered"
	var t_deliv := int(spans["packet:2"]["end_tick"]) if delivered else -1
	var t_mid := -1  # packet 2 half way: hand the A label over to B
	if spans.has("packet:2") and (delivered or (p2_active and u2 >= 0.5)):
		var s: Dictionary = spans["packet:2"]
		t_mid = int(s["begin_tick"]) + (int(s["end_tick"]) - int(s["begin_tick"])) / 2

	# Visibility windows, keyed to trace events (<= 4 content labels at any tick):
	#   [0, cut)            A, B, primary
	#   [cut, timeout)      A, primary, status
	#   [timeout, mid)      A, secondary, status
	#   [mid, delivered)    B, secondary, status
	#   [delivered, end)    A, B, status
	var a_on := 0
	if delivered:
		a_on = t_deliv
	elif t_mid >= 0:
		a_on = -1
	var on := {
		"A": a_on,
		"B": 0 if t_fault < 0 else t_mid,
		"primary": 0 if t_timeout < 0 else -1,
		"secondary": t_timeout if (t_timeout >= 0 and not delivered) else -1,
	}
	var top_y := BODY.y + KICK_H + 0.05
	var anchors := {
		"A": Vector3(-NODE_X - 0.35, top_y, BODY.z * 0.5 - 0.1),
		"B": Vector3(NODE_X + 0.35, top_y, BODY.z * 0.5 - 0.1),
		"primary": _link_point("link:P", 0.3) + Vector3(0, -CABLE_R, CABLE_R * 0.6),
		"secondary": _link_point("link:S", 0.6) + Vector3(0, CABLE_R, 0),
	}
	# Leader geometry in px at 1080: (stub: sign = side of the label, rise: negative = up).
	var geo := {
		"A": Vector2(28, -64),
		"B": Vector2(-28, -64),
		"primary": Vector2(28, 92),
		"secondary": Vector2(28, -92),
	}
	var safe := Rect2(Vector2.ZERO, vp).grow(-MARGIN * ls)
	if tall:
		safe = Rect2(MARGIN * ls, 0.14 * vp.y + 190.0 * ls, vp.x - 2.0 * MARGIN * ls, 0.66 * vp.y - 190.0 * ls - 140.0 * ls)
	var k := ls * (1.3 if tall else 1.0)
	for key in lbl:
		var l: Label = lbl[key]
		var ln: Line2D = leaders[key]
		var dot: Control = dots[key]
		var a := _fade(t, on[key])
		var world: Vector3 = anchors[key]
		var vis := a > 0.0 and not cam.is_position_behind(world)
		var sp := cam.unproject_position(world)
		var g: Vector2 = geo[key] * k
		var ms := l.get_minimum_size()
		var elbow := sp + Vector2(0, g.y)
		var stub_end := elbow + Vector2(g.x, 0)
		var gap := 10.0 * k
		var lx := stub_end.x + gap if g.x > 0 else stub_end.x - gap - ms.x
		var lp := Vector2(lx, elbow.y - ms.y * 0.5)
		vis = vis and safe.encloses(Rect2(lp, ms))
		l.visible = vis
		ln.visible = vis
		dot.visible = vis
		if not vis:
			continue
		l.position = lp
		l.modulate.a = a
		var r := 4.0 * k
		var start := sp + Vector2(0, signf(g.y) * r)
		ln.points = PackedVector2Array([start.round() + Vector2(0.5, 0.5), elbow.round() + Vector2(0.5, 0.5), stub_end.round() + Vector2(0.5, 0.5)])
		ln.modulate.a = a
		dot.position = sp - dot.size * 0.5
		dot.modulate.a = a

	# Status line: changes only on trace events; its marker shape encodes the state.
	var key_now := ""
	var t_on := -1
	var kind := "dot"
	var mark: Color = col["annotation"]
	if delivered:
		key_now = "delivered" if t_ack >= 0 else "delivered_noack"
		t_on = t_deliv if t_ack < 0 else t_ack
		kind = "dot"
		mark = col["recovered"]
	elif t_timeout >= 0:
		key_now = "reroute"
		t_on = t_timeout
		kind = "ring"
		mark = col["annotation"]
	elif t_fault >= 0:
		key_now = "cut"
		t_on = t_fault
		kind = "dash"
		mark = col["fault"]
	for sk in status_lbl:
		var sl: Label = status_lbl[sk]
		sl.visible = sk == key_now
		if sk != key_now:
			continue
		var ms := sl.get_minimum_size()
		var y := (0.8 * vp.y - 8.0 * ls - ms.y) if tall else (vp.y - MARGIN * ls - ms.y)
		var x := (vp.x - ms.x) * 0.5 + (status_mark.size.x + 18.0 * ls) * 0.5
		sl.position = Vector2(x, y)
		# The delivered line upgrades to "acknowledged" without a fade (same line, more facts).
		sl.modulate.a = 1.0 if sk == "delivered" else _fade(t, t_on)
		status_mark.visible = true
		status_mark.set_mark(kind, mark, maxf(2.0, 2.0 * lw))
		status_mark.position = Vector2(x - status_mark.size.x - 18.0 * ls, y + (ms.y - status_mark.size.y) * 0.5)
		status_mark.modulate.a = sl.modulate.a
	if key_now == "":
		status_mark.visible = false
