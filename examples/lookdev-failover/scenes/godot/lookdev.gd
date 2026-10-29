extends Node3D
## Look-development treatment of the failover trace (shot L1, examples/lookdev-failover).
##
## Trace-driven: packet positions, conduit glow, link status, fault/timeout/reroute/ack
## effects and the status line all read `Kinetic` (state, active_span, signals_in) or the
## resolved spans file at the current tick. Illustrative: the camera, label fades and the
## decay envelopes of one-shot effects. Everything is a pure function of `Kinetic.tick`
## (never of accumulated frame time). Rationale and colour roles: ../../LOOKDEV.md.

const NODE_X := 4.4  # node centres at x = -4.4 (A) and +4.4 (B)
const NODE_HALF := 0.9  # half width of a node slab
const NODE_H := 1.9  # slab height above the plinth
const PLINTH_H := 0.12
const PORT_X := NODE_X - NODE_HALF  # conduit ends at the slab faces
const P_Y := 0.42  # primary conduit: low, in front
const P_Z := 0.3
const S_Y := 1.42  # secondary conduit: high, behind
const S_Z := -0.3
const CONDUIT_R := 0.075
const TRAIL_TICKS := 150  # trail = distance travelled over the last 150 ticks of the trace

const CONDUIT_SHADER := """
shader_type spatial;
render_mode cull_back;
uniform vec3 sheath_col : source_color = vec3(0.07, 0.075, 0.085);
uniform float sheath_rough = 0.38;
uniform float sheath_metal = 0.55;
uniform float length_units = 8.2;
uniform vec3 line_col : source_color = vec3(1.0);
uniform float line_energy = 0.0;   // link status hairline
uniform float dash = 0.0;          // 1 = dashed hairline (fault / down: shape, not only hue)
uniform vec3 flow_col : source_color = vec3(1.0);
uniform float flow_a = -1.0;       // lit band [flow_a, flow_b] along the conduit, 0 = A end
uniform float flow_b = -1.0;
uniform float flow_head = -1.0;    // packet head (brightest end of the band)
uniform float flow_energy = 0.0;
uniform vec3 pulse_col : source_color = vec3(1.0);
uniform float pulse_energy = 0.0;  // one-shot fault pulse over the whole conduit
varying float t_len;
varying vec3 n_obj;
void vertex() {
	t_len = VERTEX.y / length_units + 0.5;
	n_obj = NORMAL;
}
void fragment() {
	ALBEDO = sheath_col;
	ROUGHNESS = sheath_rough;
	METALLIC = sheath_metal;
	// The mesh is rotated -90 deg about z: object -x is world up, object z is world z.
	float facing = dot(normalize(vec2(-n_obj.x, n_obj.z)), normalize(vec2(0.55, 0.85)));
	float hair = smoothstep(0.86, 0.97, facing) * step(abs(n_obj.y), 0.5);
	float dashes = mix(1.0, step(0.5, fract(t_len * length_units * 2.2)), dash);
	float core = pow(clamp(dot(NORMAL, VIEW), 0.0, 1.0), 1.4);
	float lo = min(flow_a, flow_b);
	float hi = max(flow_a, flow_b);
	float band = smoothstep(lo - 0.004, lo + 0.004, t_len) * (1.0 - smoothstep(hi - 0.004, hi + 0.004, t_len));
	float span = max(hi - lo, 0.0001);
	float ramp = 1.0 - clamp(abs(t_len - flow_head) / span, 0.0, 1.0);
	vec3 e = line_col * line_energy * hair * dashes;
	e += flow_col * flow_energy * band * (0.25 + 0.75 * ramp * ramp) * (0.45 + 0.55 * core);
	e += pulse_col * pulse_energy * (0.35 + 0.65 * core);
	EMISSION = e;
}
"""

const GROUND_SHADER := """
shader_type spatial;
uniform vec3 base : source_color = vec3(0.09);
uniform float radius = 15.0;
varying vec3 wpos;
void vertex() { wpos = (MODEL_MATRIX * vec4(VERTEX, 1.0)).xyz; }
void fragment() {
	// The stage falls off into the background: no visible horizon line.
	float r = length(wpos.xz * vec2(0.75, 1.15));
	float k = 1.0 - smoothstep(radius * 0.3, radius, r);
	ALBEDO = base * k;
	ROUGHNESS = 0.82;
	SPECULAR = 0.35 * k;
}
"""

var col := {}
var spans := {}  # entity -> resolved span dictionaries from spans.jsonl
var ready_done := false

var conduit_p: MeshInstance3D
var conduit_s: MeshInstance3D
var mat_p: ShaderMaterial
var mat_s: ShaderMaterial
var node_a: Node3D
var node_b: Node3D
var strip_a: StandardMaterial3D
var strip_b: StandardMaterial3D
var pkt := {}  # entity -> MeshInstance3D
var pkt_mat := {}  # entity -> StandardMaterial3D
var ring: MeshInstance3D
var ring_mat: StandardMaterial3D

var ui_scale := 1.0
var font_px := 40
var lbl := {}  # key -> Label
var leaders := {}  # key -> Line2D
var status_lbl := {}  # key -> Label (one per status text, so each registered text is fixed)
var status_mark: ColorRect
var badge: PanelContainer
var timescale_lbl: Label


func _ready() -> void:
	col = {
		"ground": _c("ground", "#16181b"),
		"structure": _c("structure", "#5a6068"),
		"sender": _c("sender", "#8fd8ff"),
		"receiver": _c("receiver", "#f2e6cf"),
		"in_flight": _c("in_flight", "#46bfff"),
		"standby": _c("standby", "#59606a"),
		"fault": _c("fault", "#ff6232"),
		"annotation": _c("annotation", "#e9e7e2"),
		"annotation_dim": _c("annotation_dim", "#a3a9b1"),
	}
	_load_spans()
	_build_environment()
	_build_stage()
	_build_nodes()
	_build_conduits()
	_build_packets()
	_build_ui()
	for e in ["node:A", "node:B"]:
		Kinetic.track(e, node_a if e == "node:A" else node_b)
	Kinetic.track("link:P", conduit_p)
	Kinetic.track("link:S", conduit_s)
	for e in pkt:
		Kinetic.track(e, pkt[e])
	ready_done = true
	_update()


func _process(_delta: float) -> void:
	_update()


# ---------------------------------------------------------------- trace access


func _c(role: String, fallback: String) -> Color:
	return Color(str(Kinetic.style("color." + role, fallback)))


func _load_spans() -> void:
	# Resolved spans give each transit's begin/end/outcome (needed to place a packet that
	# is lost mid-flight, see _packet_u). Read-only; same file the runtime samples.
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


func _sig_tick(sig_name: String) -> int:
	# Tick of the first `sig_name` signal at or before the current tick, or -1.
	for s in Kinetic.signals_in(-1, Kinetic.tick):
		if str(s.get("name", "")) == sig_name:
			return int(s.get("tick", -1))
	return -1


func _status(entity: String) -> String:
	return str(Kinetic.state(entity).get("status", ""))


func _packet_u(entity: String, t: int) -> float:
	# Position along the link (0 = sender end) at tick t, from the resolved span.
	# Completed transits: (t - begin) / (end - begin), identical to the span's linear
	# progress. A transit cut short (outcome "lost") ends at the loss tick, so its
	# progress reaches 1.0 at the cut; we normalise by the link's latency_ms from the trace
	# instead, which places the packet where the model says it was when it was lost.
	if not spans.has(entity):
		return -1.0
	var s: Dictionary = spans[entity]
	var b := int(s["begin_tick"])
	var e := int(s["end_tick"])
	var dur := float(e - b)
	if str(s.get("outcome", "")) == "lost":
		var via := str(s["attrs"]["via"])
		dur = float(Kinetic.state(via).get("latency_ms", e - b))
	t = clampi(t, b, e)
	return clampf(float(t - b) / maxf(dur, 1.0), 0.0, 1.0)


func _span_dir_from_b(entity: String) -> bool:
	return spans.has(entity) and str(spans[entity]["attrs"].get("from", "")) == "node:B"


func _span_via(entity: String) -> String:
	return str(spans[entity]["attrs"].get("via", "")) if spans.has(entity) else ""


# ---------------------------------------------------------------- build


func _build_environment() -> void:
	var env := Environment.new()
	env.background_mode = Environment.BG_COLOR
	env.background_color = Color("#060708")
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color("#8c97a6")
	env.ambient_light_energy = 0.1
	env.tonemap_mode = Environment.TONE_MAPPER_AGX
	env.tonemap_exposure = 1.0
	env.ssao_enabled = true
	env.ssao_radius = 0.9
	env.ssao_intensity = 1.8
	env.ssao_power = 1.4
	env.glow_enabled = true
	env.glow_intensity = 0.55
	env.glow_strength = 0.9
	env.glow_bloom = 0.0
	env.glow_hdr_threshold = 1.1
	env.glow_blend_mode = Environment.GLOW_BLEND_MODE_SCREEN
	env.fog_enabled = true
	env.fog_light_color = Color("#060708")
	env.fog_density = 0.012
	env.fog_sky_affect = 0.0
	var we := WorldEnvironment.new()
	we.environment = env
	add_child(we)

	# Key: one motivated overhead-left work light; its pool defines the stage.
	var key := SpotLight3D.new()
	key.light_color = Color("#fff1de")
	key.light_energy = 120.0
	key.spot_range = 30.0
	key.spot_angle = 44.0
	key.spot_angle_attenuation = 1.6
	key.shadow_enabled = true
	key.shadow_blur = 2.5
	add_child(key)
	key.global_transform = Transform3D(
		Basis.looking_at(Vector3(0.5, 0.4, -0.5) - Vector3(-6.5, 11.0, 7.0), Vector3.UP),
		Vector3(-6.5, 11.0, 7.0)
	)
	# Rim: cool, from behind and above, separates silhouettes from the ground.
	var rim := DirectionalLight3D.new()
	rim.light_color = Color("#a9c4ff")
	rim.light_energy = 0.35
	rim.shadow_enabled = false
	add_child(rim)
	rim.global_transform = Transform3D(
		Basis.looking_at(Vector3(0.2, -0.45, 1.0), Vector3.UP), Vector3.ZERO
	)
	# Bounce: low, warm-neutral fill from the floor in front.
	var bounce := OmniLight3D.new()
	bounce.light_color = Color("#d9cfc2")
	bounce.light_energy = 0.3
	bounce.omni_range = 11.0
	bounce.shadow_enabled = false
	bounce.position = Vector3(0.0, 3.2, 5.5)
	add_child(bounce)


func _std(albedo: Color, rough: float, metal: float) -> StandardMaterial3D:
	var m := StandardMaterial3D.new()
	m.albedo_color = albedo
	m.roughness = rough
	m.metallic = metal
	return m


func _box(size: Vector3, pos: Vector3, mat: Material, parent: Node3D) -> MeshInstance3D:
	var mi := MeshInstance3D.new()
	var bm := BoxMesh.new()
	bm.size = size
	mi.mesh = bm
	mi.material_override = mat
	mi.position = pos
	parent.add_child(mi)
	return mi


func _build_stage() -> void:
	var ground := MeshInstance3D.new()
	var pm := PlaneMesh.new()
	pm.size = Vector2(80, 80)
	ground.mesh = pm
	var gsh := Shader.new()
	gsh.code = GROUND_SHADER
	var gm := ShaderMaterial.new()
	gm.shader = gsh
	gm.set_shader_parameter("base", col["ground"])
	ground.material_override = gm
	add_child(ground)


func _build_node(x: float, strip_col: Color) -> Array:
	var root := Node3D.new()
	root.position = Vector3(x, 0, 0)
	add_child(root)
	var plinth := _std(Color("#1e2125"), 0.75, 0.0)
	var body := _std(col["structure"], 0.42, 0.05)
	var cap := _std(Color("#2b2f35"), 0.32, 0.7)
	_box(Vector3(2.5, PLINTH_H, 2.5), Vector3(0, PLINTH_H * 0.5, 0), plinth, root)
	# Slab with a stepped top: a darker metal cap reads as a bevel under the key light.
	_box(Vector3(NODE_HALF * 2, NODE_H - 0.1, 1.5), Vector3(0, PLINTH_H + (NODE_H - 0.1) * 0.5, 0), body, root)
	_box(Vector3(NODE_HALF * 2 - 0.08, 0.1, 1.42), Vector3(0, PLINTH_H + NODE_H - 0.05, 0), cap, root)
	# Recessed front panel with a vertical seam: reads as an enclosure, not a box.
	var panel := _std(Color("#363a40"), 0.55, 0.1)
	_box(Vector3(NODE_HALF * 2 - 0.3, NODE_H - 0.62, 0.02), Vector3(0, PLINTH_H + (NODE_H - 0.62) * 0.5 + 0.12, 0.751), panel, root)
	var seam := _std(Color("#1a1c20"), 0.7, 0.0)
	_box(Vector3(0.012, NODE_H - 0.62, 0.024), Vector3(0, PLINTH_H + (NODE_H - 0.62) * 0.5 + 0.12, 0.752), seam, root)
	# Status strip on the front face; its colour is the node's role.
	var strip := StandardMaterial3D.new()
	strip.albedo_color = Color(0.05, 0.05, 0.05)
	strip.emission_enabled = true
	strip.emission = strip_col
	strip.emission_energy_multiplier = 1.2
	strip.roughness = 0.3
	_box(Vector3(1.3, 0.07, 0.02), Vector3(0, PLINTH_H + NODE_H - 0.32, 0.755), strip, root)
	# Port collars where the conduits enter.
	var collar_mat := _std(Color("#3a3f46"), 0.35, 0.8)
	var face := -NODE_HALF if x > 0 else NODE_HALF
	for yz in [Vector2(P_Y, P_Z), Vector2(S_Y, S_Z)]:
		var c := MeshInstance3D.new()
		var cm := CylinderMesh.new()
		cm.top_radius = CONDUIT_R * 1.9
		cm.bottom_radius = CONDUIT_R * 1.9
		cm.height = 0.16
		c.mesh = cm
		c.material_override = collar_mat
		c.rotation = Vector3(0, 0, PI * 0.5)
		c.position = Vector3(face + signf(face) * 0.06, yz.x, yz.y)
		root.add_child(c)
	return [root, strip]


func _build_nodes() -> void:
	var a := _build_node(-NODE_X, col["sender"])
	node_a = a[0]
	strip_a = a[1]
	var b := _build_node(NODE_X, col["receiver"])
	node_b = b[0]
	strip_b = b[1]
	# Timeout ring: lies on the plinth footprint of A.
	ring = MeshInstance3D.new()
	var tm := TorusMesh.new()
	tm.inner_radius = 1.5
	tm.outer_radius = 1.54
	tm.rings = 96
	tm.ring_segments = 8
	ring.mesh = tm
	ring_mat = StandardMaterial3D.new()
	ring_mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	ring_mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	ring_mat.blend_mode = BaseMaterial3D.BLEND_MODE_ADD
	ring_mat.albedo_color = Color(col["annotation"], 0.0)
	ring.material_override = ring_mat
	ring.position = Vector3(-NODE_X, 0.03, 0)
	ring.visible = false
	add_child(ring)


func _conduit(y: float, z: float) -> Array:
	var mi := MeshInstance3D.new()
	var cm := CylinderMesh.new()
	var length := PORT_X * 2.0
	cm.top_radius = CONDUIT_R
	cm.bottom_radius = CONDUIT_R
	cm.height = length
	cm.radial_segments = 32
	cm.rings = 64
	mi.mesh = cm
	var sh := Shader.new()
	sh.code = CONDUIT_SHADER
	var m := ShaderMaterial.new()
	m.shader = sh
	m.set_shader_parameter("length_units", length)
	mi.material_override = m
	mi.rotation = Vector3(0, 0, -PI * 0.5)  # local +y -> world +x (A -> B)
	mi.position = Vector3(0, y, z)
	add_child(mi)
	# Two slim saddles carry each conduit (physical plausibility, no meaning).
	var sm := _std(Color("#24272c"), 0.6, 0.3)
	for x in [-1.6, 1.6]:
		_box(Vector3(0.06, y - 0.02, 0.06), Vector3(x, (y - 0.02) * 0.5, z), sm, self)
		_box(Vector3(0.16, 0.04, 0.2), Vector3(x, y - CONDUIT_R - 0.01, z), sm, self)
	return [mi, m]


func _build_conduits() -> void:
	var p := _conduit(P_Y, P_Z)
	conduit_p = p[0]
	mat_p = p[1]
	var s := _conduit(S_Y, S_Z)
	conduit_s = s[0]
	mat_s = s[1]


func _build_packets() -> void:
	for e in ["packet:1", "packet:2", "packet:ack2"]:
		var mi := MeshInstance3D.new()
		if e == "packet:ack2":
			var sp := SphereMesh.new()  # the ack is a different shape, not only a size
			sp.radius = 0.1
			sp.height = 0.2
			mi.mesh = sp
		else:
			var cp := CapsuleMesh.new()
			cp.radius = 0.12
			cp.height = 0.46
			mi.mesh = cp
			mi.rotation = Vector3(0, 0, PI * 0.5)
		var m := StandardMaterial3D.new()
		m.albedo_color = col["in_flight"]
		m.roughness = 0.25
		m.emission_enabled = true
		m.emission = col["in_flight"]
		m.emission_energy_multiplier = 1.6
		mi.material_override = m
		mi.visible = false
		add_child(mi)
		pkt[e] = mi
		pkt_mat[e] = m


# ---------------------------------------------------------------- UI


func _settings(px: int, c: Color) -> LabelSettings:
	var ls := LabelSettings.new()
	ls.font_size = px
	ls.font_color = c
	ls.shadow_color = Color(0, 0, 0, 0.55)
	ls.shadow_size = maxi(1, int(round(3 * ui_scale)))
	ls.shadow_offset = Vector2(0, 2 * ui_scale)
	return ls


func _new_label(layer: Node, text: String, px: int, c: Color) -> Label:
	var l := Label.new()
	l.text = text
	l.label_settings = _settings(px, c)
	l.visible = false
	layer.add_child(l)
	Kinetic.label(text, l)
	return l


func _build_ui() -> void:
	var vp := get_viewport().get_visible_rect().size
	var tall := vp.y > vp.x
	ui_scale = minf(vp.x, vp.y) / 1080.0
	# §12 minimum cap height: 28 px at 1080p, 40 px at 1080x1920 (cap ~0.71 em).
	font_px = int(round((56.0 if tall else 40.0) * ui_scale))
	var layer := CanvasLayer.new()
	add_child(layer)

	for key in ["A", "B", "primary", "secondary"]:
		var ln := Line2D.new()
		ln.width = maxf(1.5, 2.0 * ui_scale)
		ln.default_color = Color(col["annotation_dim"], 0.7)
		ln.antialiased = true
		ln.visible = false
		layer.add_child(ln)
		leaders[key] = ln
		var big: bool = key in ["A", "B"]
		lbl[key] = _new_label(layer, key, font_px, col["annotation"] if big else col["annotation_dim"])

	var texts := {
		"cut": "primary link cut, packet lost",
		"reroute": "timeout: resend on secondary",
		"delivered": "resend delivered",
	}
	for k in texts:
		status_lbl[k] = _new_label(layer, texts[k], int(round(font_px * 1.05)), col["annotation"])
	status_mark = ColorRect.new()
	status_mark.size = Vector2(14, 14) * ui_scale * (1.4 if tall else 1.0)
	status_mark.visible = false
	layer.add_child(status_mark)

	# Evidence badge: fixed top-left corner, outlined, muted.
	badge = PanelContainer.new()
	var sb := StyleBoxFlat.new()
	sb.bg_color = Color(0.04, 0.045, 0.05, 0.55)
	sb.border_color = Color(col["annotation_dim"], 0.55)
	sb.set_border_width_all(maxi(1, int(round(2 * ui_scale))))
	sb.set_corner_radius_all(int(round(6 * ui_scale)))
	sb.content_margin_left = 18 * ui_scale
	sb.content_margin_right = 18 * ui_scale
	sb.content_margin_top = 8 * ui_scale
	sb.content_margin_bottom = 8 * ui_scale
	badge.add_theme_stylebox_override("panel", sb)
	var margin := 48.0 * ui_scale
	# 9:16 keeps text inside the vertical safe area (14 %..80 % of the height).
	var top := (0.14 * vp.y + 12.0 * ui_scale) if tall else margin
	badge.position = Vector2(margin, top)
	layer.add_child(badge)
	var badge_text := _badge_text()
	var box := VBoxContainer.new()
	box.add_theme_constant_override("separation", 0)
	badge.add_child(box)
	# Landscape: one line. 9:16: class on the first line, qualifier on the second.
	var parts := [badge_text] if not tall else Array(badge_text.split(" · "))
	for i in parts.size():
		var bl := Label.new()
		bl.text = str(parts[i]) + (" ·" if tall and i < parts.size() - 1 else "")
		bl.label_settings = _settings(font_px, col["annotation_dim"])
		bl.label_settings.shadow_size = 0
		box.add_child(bl)
	badge.visible = badge_text != ""
	if badge_text != "":
		Kinetic.label(badge_text, box)

	timescale_lbl = Label.new()
	timescale_lbl.text = _timescale_text()
	timescale_lbl.label_settings = _settings(font_px, col["annotation_dim"])
	layer.add_child(timescale_lbl)
	timescale_lbl.visible = timescale_lbl.text != ""
	var ts := timescale_lbl.get_minimum_size()
	if tall:
		timescale_lbl.position = Vector2(margin + 18 * ui_scale, top + badge.get_combined_minimum_size().y + 10 * ui_scale)
	else:
		timescale_lbl.position = Vector2(vp.x - margin - ts.x, margin + 8 * ui_scale)
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
	# Fallback derived from the job: sim seconds shown / film seconds.
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
	var t := clampf((x - a) / (b - a), 0.0, 1.0)
	return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


func _update() -> void:
	if not ready_done:
		return
	var t: int = Kinetic.tick
	var t_fault := _sig_tick("fault.injected")
	var t_timeout := _sig_tick("timeout.fired")
	var t_route := _sig_tick("route.changed")
	var t_ack := _sig_tick("ack.received")

	_update_links(t, t_fault)
	_update_packets(t)
	_update_nodes(t, t_timeout, t_ack)
	_update_cameras(t, t_fault, t_timeout, t_route)
	_update_labels(t, t_fault, t_timeout)


func _update_links(t: int, t_fault: int) -> void:
	for pair in [["link:P", mat_p], ["link:S", mat_s]]:
		var link: String = pair[0]
		var m: ShaderMaterial = pair[1]
		var st := _status(link)
		var line_col: Color = col["in_flight"]
		var line_e := 0.0
		var dash := 0.0
		match st:
			"up":
				line_e = 0.55
			"standby":
				line_col = col["standby"]
				line_e = 0.25
				dash = 1.0
			"failed":
				line_col = col["fault"]
				line_e = 1.1
				dash = 1.0
			"down":
				line_col = col["fault"]
				line_e = 0.45
				dash = 1.0
		m.set_shader_parameter("line_col", line_col)
		m.set_shader_parameter("line_energy", line_e)
		m.set_shader_parameter("dash", dash)
		# Fault pulse: one decaying flash at the fault tick (envelope is illustrative).
		var pulse := 0.0
		if link == "link:P" and t_fault >= 0:
			var age := float(t - t_fault)
			pulse = 1.8 * exp(-age / 70.0)
		m.set_shader_parameter("pulse_col", col["fault"])
		m.set_shader_parameter("pulse_energy", pulse)
		# Data glow only where a packet is on this link right now.
		var lit := false
		for e in pkt:
			if _span_via(e) != link or Kinetic.active_span(e).is_empty():
				continue
			var u := _packet_u(e, t)
			var u_tail := _packet_u(e, t - TRAIL_TICKS)
			if _span_dir_from_b(e):
				u = 1.0 - u
				u_tail = 1.0 - u_tail
			m.set_shader_parameter("flow_a", u_tail)
			m.set_shader_parameter("flow_b", u)
			m.set_shader_parameter("flow_head", u)
			m.set_shader_parameter("flow_col", col["in_flight"])
			m.set_shader_parameter("flow_energy", 3.2)
			lit = true
		if not lit:
			m.set_shader_parameter("flow_energy", 0.0)
			m.set_shader_parameter("flow_a", -1.0)
			m.set_shader_parameter("flow_b", -1.0)


func _link_point(link: String, u: float) -> Vector3:
	var y := P_Y if link == "link:P" else S_Y
	var z := P_Z if link == "link:P" else S_Z
	return Vector3(lerpf(-PORT_X, PORT_X, u), y, z)


func _update_packets(t: int) -> void:
	for e in pkt:
		var mi: MeshInstance3D = pkt[e]
		var m: StandardMaterial3D = pkt_mat[e]
		if not Kinetic.alive(e) or not spans.has(e):
			mi.visible = false
			continue
		var u := _packet_u(e, t)
		if _span_dir_from_b(e):
			u = 1.0 - u
		mi.position = _link_point(_span_via(e), u)
		var st := _status(e)
		mi.scale = Vector3.ONE
		m.emission = col["in_flight"]
		m.albedo_color = col["in_flight"]
		m.emission_energy_multiplier = 1.6
		if st == "in_flight":
			mi.visible = true
		elif st == "lost":
			# Stays where it was lost, takes the fault colour and dims out (illustrative fade).
			var age := float(t - int(spans[e]["end_tick"]))
			var k := clampf(age / 380.0, 0.0, 1.0)
			mi.visible = k < 1.0
			m.emission = col["fault"]
			m.emission_energy_multiplier = 1.6 * (1.0 - k)
			m.albedo_color = col["fault"].lerp(Color(0.1, 0.09, 0.09), k)
			mi.scale = Vector3.ONE * (1.0 - 0.45 * k)
		else:
			# Delivered: docked in the receiving port, unlit (state is shown, not motion).
			mi.visible = true
			m.emission_energy_multiplier = 0.35
			m.albedo_color = col["in_flight"].darkened(0.35)


func _update_nodes(t: int, t_timeout: int, t_ack: int) -> void:
	# Receipt pulses on the status strips: B when packet 2 is delivered, A on ack.received.
	var b_pulse := 0.0
	if spans.has("packet:2") and _status("packet:2") == "delivered":
		b_pulse = exp(-float(t - int(spans["packet:2"]["end_tick"])) / 220.0)
	var a_pulse := 0.0
	if t_ack >= 0:
		a_pulse = exp(-float(t - t_ack) / 220.0)
	strip_a.emission_energy_multiplier = 1.2 + 4.0 * a_pulse
	strip_b.emission_energy_multiplier = 1.2 + 4.0 * b_pulse
	# Timeout: a calm ring pulse spreading from A's footprint.
	if t_timeout >= 0 and t - t_timeout < 520:
		var s := float(t - t_timeout) / 520.0
		ring.visible = true
		var r := 1.0 + 0.75 * _smooth(0.0, 1.0, s)
		ring.scale = Vector3(r, 1.0, r)
		ring_mat.albedo_color = Color(col["annotation"], 0.9 * pow(1.0 - s, 1.6))
	else:
		ring.visible = false


# Camera poses: [position, target, fov]. The lock-off is the default; the camera moves only
# to reveal a cause (A reacting to the loss) and to follow the resend.
func _poses(cam: String) -> Dictionary:
	if cam == "tall":
		return {
			# Looking down the link from A's side: A near and low, B far and high.
			"wide": [Vector3(-10.0, 4.6, 6.4), Vector3(0.6, 0.9, 0), 46.0],
			"a": [Vector3(-8.2, 3.0, 4.6), Vector3(-2.6, 1.0, 0), 50.0],
			"track0": [Vector3(-8.6, 3.8, 5.4), Vector3(-1.4, 1.0, 0), 48.0],
			"track1": [Vector3(-6.0, 4.4, 6.0), Vector3(2.2, 1.0, 0), 48.0],
			"end": [Vector3(-10.6, 5.0, 6.8), Vector3(0.6, 0.9, 0), 46.0],
		}
	return {
		"wide": [Vector3(0, 2.9, 10.6), Vector3(0, 1.0, 0), 38.0],
		"a": [Vector3(-0.9, 1.9, 5.9), Vector3(-2.7, 0.95, 0), 40.0],
		"track0": [Vector3(-2.0, 2.5, 7.6), Vector3(-1.8, 1.0, 0), 38.0],
		"track1": [Vector3(2.0, 2.5, 7.6), Vector3(1.8, 1.0, 0), 38.0],
		"end": [Vector3(0, 3.4, 11.4), Vector3(0, 1.0, 0), 38.0],
	}


static func _mix(p: Array, q: Array, w: float) -> Array:
	return [p[0].lerp(q[0], w), p[1].lerp(q[1], w), lerpf(p[2], q[2], w)]


func _cam_pose(cam: String, t: int, t_fault: int, t_timeout: int, t_route: int) -> Array:
	var P := _poses(cam)
	var u2 := 0.0  # packet 2 progress (trace), 1 once delivered
	if not Kinetic.active_span("packet:2").is_empty():
		u2 = _packet_u("packet:2", t)
	elif _status("packet:2") == "delivered":
		u2 = 1.0
	var u_ack := 0.0
	if not Kinetic.active_span("packet:ack2").is_empty():
		u_ack = _packet_u("packet:ack2", t)
	elif _status("packet:ack2") == "delivered":
		u_ack = 1.0

	if Kinetic.calm:
		# Reduced motion: three static framings, cut on trace events. Wide through the loss
		# (the lost packet must stay in frame), the secondary from the timeout to delivery,
		# wide again for the ack.
		var t_timeout_c := _sig_tick("timeout.fired")
		if t_timeout_c >= 0 and u2 < 1.0:
			return _mix(P["track0"], P["track1"], 0.5)
		return P["wide"] if t_timeout_c < 0 else P["end"]

	var pose: Array = P["wide"]
	if t_fault >= 0:
		# Push toward A between the cut and the timeout, arriving as the timer fires.
		# The timeout tick is not known before it fires, so the push is keyed to the cut and
		# lasts 260 ticks; in this trace it lands as timeout.fired arrives (1200 + 300).
		pose = _mix(pose, P["a"], _smooth(float(t_fault + 40), float(t_fault + 300), float(t)))
	if t_route >= 0:
		# Follow the resend along the secondary; the dolly runs on packet 2's progress.
		var track := _mix(P["track0"], P["track1"], _smooth(0.0, 1.0, u2))
		pose = _mix(pose, track, _smooth(0.0, 0.18, u2))
	if u2 >= 1.0:
		# Settle wide as the ack returns.
		pose = _mix(pose, P["end"], _smooth(0.0, 0.55, u_ack))
	return pose


func _update_cameras(t: int, t_fault: int, t_timeout: int, t_route: int) -> void:
	for cam_name in ["hero", "tall"]:
		var cam := get_node_or_null(cam_name) as Camera3D
		if cam == null:
			continue
		var p := _cam_pose(cam_name, t, t_fault, t_timeout, t_route)
		cam.global_transform = Transform3D(Basis.looking_at(p[1] - p[0], Vector3.UP), p[0])
		cam.fov = p[2]


func _fade(t: int, t_on: int) -> float:
	# Labels fade in over 60 ticks; they cut out (no cross-fades, so the count never exceeds 4).
	if t_on < 0 or t < t_on:
		return 0.0
	return clampf(float(t - t_on) / 60.0, 0.0, 1.0)


func _update_labels(t: int, t_fault: int, t_timeout: int) -> void:
	var cam := get_viewport().get_camera_3d()
	if cam == null:
		return
	var vp := get_viewport().get_visible_rect().size
	var tall := vp.y > vp.x
	var p2_active := not Kinetic.active_span("packet:2").is_empty()
	var u2 := _packet_u("packet:2", t) if p2_active else 0.0
	var delivered := _status("packet:2") == "delivered"
	var t_deliv := int(spans["packet:2"]["end_tick"]) if delivered else -1
	var t_mid := -1  # packet 2 half way: hand the A label over to B
	if spans.has("packet:2") and (delivered or (p2_active and u2 >= 0.5)):
		var s: Dictionary = spans["packet:2"]
		t_mid = int(s["begin_tick"]) + (int(s["end_tick"]) - int(s["begin_tick"])) / 2

	# Visibility windows, keyed to trace events (<= 4 labels besides the badge at any tick):
	#   [0, cut)            A, B, primary            + time scale
	#   [cut, timeout)      A, primary, status       + time scale
	#   [timeout, mid)      A, secondary, status     + time scale
	#   [mid, delivered)    B, secondary, status     + time scale
	#   [delivered, end)    A, B, status             + time scale
	var a_on := 0
	if delivered:
		a_on = t_deliv
	elif t_mid >= 0:
		a_on = -1
	var on := {
		"A": a_on,
		"B": 0 if t_fault < 0 else t_mid,  # -1 (hidden) until packet 2 is half way
		"primary": 0 if t_timeout < 0 else -1,
		"secondary": t_timeout if (t_timeout >= 0 and not delivered) else -1,
	}

	var anchors := {
		"A": Vector3(-NODE_X, PLINTH_H + NODE_H + 0.02, 0),
		"B": Vector3(NODE_X, PLINTH_H + NODE_H + 0.02, 0),
		"primary": _link_point("link:P", 0.36) + Vector3(0, -CONDUIT_R, CONDUIT_R),
		"secondary": _link_point("link:S", 0.64) + Vector3(0, CONDUIT_R, 0),
	}
	var offsets := {  # label offset from the anchor in px at 1080p (leader line between)
		"A": Vector2(0, -70),
		"B": Vector2(0, -70),
		"primary": Vector2(-40, 120),
		"secondary": Vector2(40, -130),
	}
	for key in lbl:
		var l: Label = lbl[key]
		var ln: Line2D = leaders[key]
		var a := _fade(t, on[key])
		var world: Vector3 = anchors[key]
		var vis := a > 0.0 and not cam.is_position_behind(world)
		var sp := cam.unproject_position(world)
		var ms := l.get_minimum_size()
		var off: Vector2 = offsets[key] * ui_scale * (1.3 if tall else 1.0)
		var lp := sp + off - Vector2(ms.x * 0.5, ms.y * (1.0 if off.y < 0 else 0.0))
		var safe := Rect2(Vector2.ZERO, vp).grow(-64.0 * ui_scale)
		if tall:
			safe = Rect2(64.0 * ui_scale, 0.14 * vp.y + 260.0 * ui_scale, vp.x - 128.0 * ui_scale, 0.66 * vp.y - 260.0 * ui_scale)
		var inside := safe.encloses(Rect2(lp, ms))
		vis = vis and inside
		l.visible = vis
		ln.visible = vis
		if not vis:
			continue
		l.position = lp
		l.modulate.a = a
		var tip := sp + off.normalized() * 10.0 * ui_scale
		var foot := sp + off + Vector2(0, (6.0 if off.y < 0 else -6.0) * ui_scale)
		ln.points = PackedVector2Array([foot, tip])
		ln.modulate.a = a

	# Status line: changes only on trace events.
	var key_now := ""
	var t_on := -1
	var mark := Color.WHITE
	if delivered:
		key_now = "delivered"
		t_on = t_deliv
		mark = col["in_flight"]
	elif t_timeout >= 0:
		key_now = "reroute"
		t_on = t_timeout
		mark = col["annotation"]
	elif t_fault >= 0:
		key_now = "cut"
		t_on = t_fault
		mark = col["fault"]
	for k in status_lbl:
		var sl: Label = status_lbl[k]
		sl.visible = k == key_now
		if k != key_now:
			continue
		var ms := sl.get_minimum_size()
		var y := (0.8 * vp.y - 28.0 * ui_scale - ms.y) if tall else (vp.y - 92.0 * ui_scale - ms.y)
		var x := (vp.x - ms.x) * 0.5 + 14.0 * ui_scale
		sl.position = Vector2(x, y)
		sl.modulate.a = _fade(t, t_on)
		status_mark.visible = true
		status_mark.color = mark
		status_mark.position = Vector2(x - status_mark.size.x - 14.0 * ui_scale, y + (ms.y - status_mark.size.y) * 0.5)
		status_mark.modulate.a = sl.modulate.a
	if key_now == "":
		status_mark.visible = false
