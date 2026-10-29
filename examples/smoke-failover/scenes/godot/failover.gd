## Smoke-failover shot: node A sends a packet to node B over the primary link P; P is cut,
## the resend crosses the secondary link S and B acknowledges over S.
##
## Everything that carries meaning (link status, which packet is where) is read from Kinetic
## each frame. Camera motion is illustrative and driven by Kinetic.shot_progress.
## The 16:9 framing lays A and B out left to right; 9:16 rotates the rig so A is on top.
extends Node3D

const A_POS := Vector3(-3.0, 0.0, 0.0)
const B_POS := Vector3(3.0, 0.0, 0.0)
const NODE_SIZE := 1.1
const TUBE_RADIUS := 0.07
const PULSE_RADIUS := 0.18
const PACKETS := ["packet:1", "packet:2", "packet:ack2"]

const COLOR_NODE := Color(0.42, 0.47, 0.56)
const COLOR_LINK_UP := Color(0.35, 0.6, 0.95)
const COLOR_LINK_STANDBY := Color(0.22, 0.25, 0.3)
const COLOR_FAULT := Color(0.95, 0.22, 0.18)
const COLOR_PULSE := Color(1.0, 0.8, 0.3)
const COLOR_ACK := Color(0.35, 1.0, 0.55)

var _rig: Node3D
var _links: Dictionary = {}  # "link:P" -> {curve, from, to, material}
var _pulses: Dictionary = {}  # packet id -> MeshInstance3D

@onready var _cam_wide: Camera3D = $wide
@onready var _cam_tall: Camera3D = $tall


func _ready() -> void:
	$Key.rotation_degrees = Vector3(-50.0, -35.0, 0.0)
	$Rim.position = Vector3(0.0, 3.5, -4.5)
	_rig = Node3D.new()
	_rig.name = "Rig"
	add_child(_rig)
	if str(Kinetic.job.get("framing", "16:9")) == "9:16":
		_rig.rotation.z = -PI / 2.0  # A on top, B below, S bulges to the right

	Kinetic.track("node:A", _add_node("A", A_POS))
	Kinetic.track("node:B", _add_node("B", B_POS))

	var edge := Vector3(NODE_SIZE / 2.0 + 0.05, 0.0, 0.0)
	var p := Curve3D.new()
	p.add_point(A_POS + edge)
	p.add_point(B_POS - edge)
	Kinetic.track("link:P", _add_link("link:P", p))

	var s := Curve3D.new()
	s.add_point(A_POS + Vector3(0.35, 0.6, 0.0), Vector3.ZERO, Vector3(0.6, 1.2, 0.0))
	s.add_point(Vector3(0.0, 2.1, 0.0), Vector3(-1.8, 0.0, 0.0), Vector3(1.8, 0.0, 0.0))
	s.add_point(B_POS + Vector3(-0.35, 0.6, 0.0), Vector3(-0.6, 1.2, 0.0), Vector3.ZERO)
	Kinetic.track("link:S", _add_link("link:S", s))

	for id: String in PACKETS:
		var pulse := _add_pulse(id, COLOR_ACK if id.ends_with("ack2") else COLOR_PULSE)
		Kinetic.track(id, pulse)
	_add_badges()
	_update()


func _process(_delta: float) -> void:
	_update()


func _update() -> void:
	var p_status := str(Kinetic.state("link:P").get("status", ""))
	var p_mat: StandardMaterial3D = _links["link:P"]["material"]
	var p_fault := p_status == "failed" or p_status == "down"
	_set_link_color(p_mat, COLOR_FAULT if p_fault else COLOR_LINK_UP, 1.2 if p_fault else 0.4)

	var s_up := str(Kinetic.state("link:S").get("status", "")) == "up"
	var s_mat: StandardMaterial3D = _links["link:S"]["material"]
	_set_link_color(s_mat, COLOR_LINK_UP if s_up else COLOR_LINK_STANDBY, 0.6 if s_up else 0.0)

	for id: String in _pulses:
		var pulse: MeshInstance3D = _pulses[id]
		var span := Kinetic.active_span(id)
		var docked := false
		if span.is_empty() and str(Kinetic.state(id).get("status", "")) == "delivered":
			# A delivered packet stays shown, docked at its destination end of the link,
			# so the "delivered" state is visible rather than implied by absence.
			span = Kinetic.last_span(id)
			docked = true
		var via := str(span.get("via", ""))
		if span.is_empty() or not _links.has(via):
			pulse.visible = false
			continue
		var link: Dictionary = _links[via]
		var u := float(span["progress"])
		pulse.scale = Vector3.ONE * (0.6 if docked else 1.0)
		if str(span.get("from", "")) == link["to"]:
			u = 1.0 - u  # travelling against the curve's direction (B -> A)
		var curve: Curve3D = link["curve"]
		pulse.position = curve.sample_baked(u * curve.get_baked_length())
		pulse.visible = true

	# Illustrative slow dolly; a quarter of the travel in the reduced-motion variant.
	var dolly := Kinetic.shot_progress * (0.25 if Kinetic.calm else 1.0)
	_cam_wide.position = Vector3(-0.6 + 1.2 * dolly, 2.4, 9.0 - 1.2 * dolly)
	_cam_wide.look_at(Vector3(0.0, 0.55, 0.0))
	_cam_tall.position = Vector3(1.4, 0.3 + 0.4 * dolly, 13.0 - 1.0 * dolly)
	_cam_tall.look_at(Vector3(0.9, 0.0, 0.0))


func _add_node(node_name: String, pos: Vector3) -> MeshInstance3D:
	var mesh := BoxMesh.new()
	mesh.size = Vector3.ONE * NODE_SIZE
	var mat := StandardMaterial3D.new()
	mat.albedo_color = COLOR_NODE
	mat.metallic = 0.2
	mat.roughness = 0.45
	mesh.material = mat
	var inst := MeshInstance3D.new()
	inst.name = node_name
	inst.mesh = mesh
	inst.position = pos
	_rig.add_child(inst)
	return inst


func _add_link(id: String, curve: Curve3D) -> MeshInstance3D:
	curve.bake_interval = 0.05
	var mat := StandardMaterial3D.new()
	mat.roughness = 0.35
	mat.cull_mode = BaseMaterial3D.CULL_DISABLED
	mat.emission_enabled = true
	var inst := MeshInstance3D.new()
	inst.name = id.replace(":", "_")
	inst.mesh = _tube_mesh(curve, TUBE_RADIUS)
	inst.material_override = mat
	_rig.add_child(inst)
	_links[id] = {"curve": curve, "from": "node:A", "to": "node:B", "material": mat}
	return inst


func _add_pulse(id: String, color: Color) -> MeshInstance3D:
	var mesh := SphereMesh.new()
	mesh.radius = PULSE_RADIUS
	mesh.height = PULSE_RADIUS * 2.0
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.emission_enabled = true
	mat.emission = color
	mat.emission_energy_multiplier = 2.5
	mesh.material = mat
	var inst := MeshInstance3D.new()
	inst.name = id.replace(":", "_")
	inst.mesh = mesh
	inst.visible = false
	_rig.add_child(inst)
	_pulses[id] = inst
	return inst


## Evidence class and time-scale label from the job (never hard-coded), top left.
func _add_badges() -> void:
	var lines := PackedStringArray()
	if Kinetic.evidence != "":
		lines.append(Kinetic.evidence.to_upper())
	if Kinetic.time_label != "":
		lines.append(Kinetic.time_label)
	if lines.is_empty():
		return
	var size := get_viewport().get_visible_rect().size
	var layer := CanvasLayer.new()
	add_child(layer)
	var badge := Label.new()
	badge.text = "\n".join(lines)
	badge.add_theme_font_size_override("font_size", maxi(10, int(size.y * 0.03)))
	badge.add_theme_color_override("font_color", Color(0.75, 0.8, 0.88))
	badge.position = size * 0.05
	if size.y > size.x:
		# 9:16: platform UI covers the top 14% and bottom 20%; sit just below the top band.
		# Right half: node A and the start of link S occupy the left side at this height.
		badge.position = Vector2(size.x * 0.62, size.y * 0.16)
	layer.add_child(badge)
	Kinetic.label(badge.text, badge)


func _set_link_color(mat: StandardMaterial3D, color: Color, glow: float) -> void:
	mat.albedo_color = color
	mat.emission = color
	mat.emission_energy_multiplier = glow


## Tube along a curve that lies in the XY plane (rings oriented with a fixed +Z binormal).
func _tube_mesh(curve: Curve3D, radius: float, sides: int = 12) -> ArrayMesh:
	var length := curve.get_baked_length()
	var rings := maxi(2, int(length / 0.1))
	var verts := PackedVector3Array()
	var normals := PackedVector3Array()
	var indices := PackedInt32Array()
	var binormal := Vector3(0.0, 0.0, 1.0)
	for i in rings + 1:
		var d := length * i / rings
		var ahead := curve.sample_baked(minf(d + 0.02, length))
		var behind := curve.sample_baked(maxf(d - 0.02, 0.0))
		var tangent := (ahead - behind).normalized()
		var normal := binormal.cross(tangent).normalized()
		var center := curve.sample_baked(d)
		for s in sides:
			var a := TAU * s / sides
			var off := normal * cos(a) + binormal * sin(a)
			verts.append(center + off * radius)
			normals.append(off)
	for i in rings:
		for s in sides:
			var a0 := i * sides + s
			var a1 := i * sides + (s + 1) % sides
			indices.append_array([a0, a1, a0 + sides, a1, a1 + sides, a0 + sides])
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = verts
	arrays[Mesh.ARRAY_NORMAL] = normals
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh := ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)
	return mesh
