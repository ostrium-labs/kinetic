## Kinetic runtime singleton (autoload "Kinetic"). Contract: docs/runtime-api.md.
##
## Frame order (verified by the smoke test, see tests/integration/test_godot_render.py):
##   1. _process here, process_priority -1000: frame, tick and the sampled trace are updated.
##   2. _process of scene nodes (priority 0): they read Kinetic and move/style themselves.
##   3. RenderingServer.frame_pre_draw: annotations for this frame are collected from the
##      final node transforms and the current camera, then the frame is drawn and Movie Maker
##      writes it. So each annotation line describes exactly the pixels of its frame.
## Without a --kinetic-job argument (editor, tools) the singleton stays idle.
extends Node

const KineticTrace := preload("trace.gd")
const JsonLite := preload("json_lite.gd")

const EXIT_BAD_JOB := 2
const EXIT_BAD_TRACE := 4
const ERROR_FILE := "runtime_error.txt"
const ANNOTATIONS_FILE := "annotations.jsonl"

## Parsed job.json.
var job: Dictionary = {}
var frame := 0
var frame_count := 0
## Sim tick shown on this frame; -1 when the shot has no trace.
var tick := -1
var sim_seconds := 0.0
## frame / frame_count, for illustrative motion only.
var shot_progress := 0.0
var calm := false
## The shot's evidence class (draw its badge) and the time-scale label to show when sim speed
## is not 1; "" when the job has none. Same values as job.evidence / job.time_label.
var evidence := ""
var time_label := ""
## A job was loaded (even if it later failed).
var active := false
var error := ""
var error_code := 0
var trace: KineticTrace = null

var _job_path := ""
var _has_clock := false
var _tick_start := 0
var _tick_end := 0
var _state: Dictionary = {}
var _spans: Array = []
var _active_by_entity: Dictionary = {}
var _tracked: Dictionary = {}
var _labels: Array = []
var _annotations: FileAccess = null
var _last_annotated := -1


func _ready() -> void:
	process_priority = -1000
	process_mode = Node.PROCESS_MODE_ALWAYS
	_job_path = _job_arg()
	if _job_path == "":
		return
	active = true
	if not _load_job(_job_path):
		return
	RenderingServer.frame_pre_draw.connect(_on_frame_pre_draw)
	_update(0)


func _process(_delta: float) -> void:
	if active and error == "":
		_update(Engine.get_frames_drawn())


func _exit_tree() -> void:
	if _annotations != null:
		_annotations.close()
		_annotations = null


# ---------------------------------------------------------------- API (docs/runtime-api.md)


## Entity state at the current tick; {} if the entity is not alive.
func state(entity: String) -> Dictionary:
	return _state.get(entity, {}).duplicate()


func alive(entity: String) -> bool:
	return _state.has(entity)


## The entity's active span (fields, attrs and "progress"), or {}.
func active_span(entity: String) -> Dictionary:
	return _active_by_entity.get(entity, {}).duplicate()


## All active spans, each with "entity".
func spans_active() -> Array:
	return _spans.duplicate(true)


## `signal` events with from_tick < tick <= to_tick.
func signals_in(from_tick: int, to_tick: int) -> Array:
	return [] if trace == null else trace.signals_in(from_tick, to_tick)


## Report this node's screen-space bbox for `entity` in every frame's annotation.
func track(entity: String, node: Node3D) -> void:
	_tracked[entity] = node


## Report an on-screen label's rect in every frame's annotation.
func label(text: String, control: Control) -> void:
	_labels.append([text, control])


func style(key: String, default: Variant = null) -> Variant:
	var tokens: Variant = job.get("style", {})
	return tokens.get(key, default) if tokens is Dictionary else default


## Record a fatal error in runtime_error.txt and quit with `code`.
func fail(message: String, code: int) -> void:
	error = message
	error_code = code
	printerr("kinetic: ", message)
	var out_dir := str(job.get("out_dir", "")) if job.get("out_dir") != null else ""
	if out_dir == "" and _job_path != "":
		out_dir = _job_path.get_base_dir()
	if out_dir != "":
		var f := FileAccess.open(out_dir.path_join(ERROR_FILE), FileAccess.WRITE)
		if f != null:
			f.store_line(message)
			f.close()
	get_tree().quit(code)


# ---------------------------------------------------------------- internals


func _job_arg() -> String:
	var args := OS.get_cmdline_user_args()
	for i in args.size():
		if args[i] == "--kinetic-job" and i + 1 < args.size():
			return args[i + 1]
		if args[i].begins_with("--kinetic-job="):
			return args[i].trim_prefix("--kinetic-job=")
	return ""


func _set_error(message: String, code: int) -> bool:
	error = message
	error_code = code
	printerr("kinetic: ", message)
	return false


func _load_job(path: String) -> bool:
	var text := FileAccess.get_file_as_string(path)
	if text == "":
		return _set_error("cannot read job %s" % path, EXIT_BAD_JOB)
	var reader := JsonLite.new()
	var parsed: Variant = reader.parse(text)
	if reader.error != "" or not parsed is Dictionary:
		return _set_error("job %s is not a JSON object: %s" % [path, reader.error], EXIT_BAD_JOB)
	job = parsed
	if str(job.get("contract", "")) != "0.1":
		return _set_error("unsupported job contract %s" % job.get("contract"), EXIT_BAD_JOB)
	for key in ["scene", "camera", "out_dir", "frame_count"]:
		if job.get(key) == null:
			return _set_error("job is missing %s" % key, EXIT_BAD_JOB)
	if not job["frame_count"] is int or job["frame_count"] <= 0:
		return _set_error("job frame_count must be a positive integer", EXIT_BAD_JOB)
	frame_count = job["frame_count"]
	calm = bool(job.get("variant_calm", false))
	evidence = str(job["evidence"]) if job.get("evidence") != null else ""
	time_label = str(job["time_label"]) if job.get("time_label") != null else ""

	var trace_dir: Variant = job.get("trace_dir")
	if trace_dir != null:
		trace = KineticTrace.new()
		if not trace.load_dir(str(trace_dir)):
			return _set_error("trace: " + trace.error, EXIT_BAD_TRACE)
		var ts: Variant = job.get("tick_start")
		var te: Variant = job.get("tick_end")
		if not (ts is int and te is int and te > ts):
			return _set_error("job with a trace needs tick_start < tick_end", EXIT_BAD_JOB)
		_has_clock = true
		_tick_start = ts
		_tick_end = te

	if bool(job.get("annotate", true)):
		var out_path := str(job["out_dir"]).path_join(ANNOTATIONS_FILE)
		_annotations = FileAccess.open(out_path, FileAccess.WRITE)
		if _annotations == null:
			var reason := error_string(FileAccess.get_open_error())
			return _set_error("cannot write %s: %s" % [out_path, reason], EXIT_BAD_JOB)
	return true


func _update(f: int) -> void:
	frame = f
	shot_progress = float(f) / float(frame_count)
	if not _has_clock:
		return
	# Exact integer mapping (job.py ShotJob.tick_at); never accumulated from deltas.
	var g := mini(f, frame_count - 1)
	tick = _tick_start + ((_tick_end - _tick_start) * g) / frame_count
	sim_seconds = trace.seconds(tick)
	_state = trace.state_at(tick)
	_spans = trace.spans_active(tick)
	_active_by_entity = {}
	for span: Dictionary in _spans:
		var entity: String = span["entity"]
		if _active_by_entity.has(entity):
			push_warning("%s has more than one active span at tick %d" % [entity, tick])
			continue
		var view := span.duplicate()
		view.erase("entity")
		_active_by_entity[entity] = view


func _on_frame_pre_draw() -> void:
	if _annotations == null or error != "" or frame >= frame_count or frame == _last_annotated:
		return
	_last_annotated = frame
	var entities := {}
	for entity: String in _tracked:
		var box := _screen_box(_tracked[entity])
		entities[entity] = {
			"state": _state.get(entity, {}),
			"active_span": _active_by_entity.get(entity),
			"bbox": box[0],
			"visible": box[1],
		}
	var labels := []
	for item: Array in _labels:
		var control: Control = item[1]
		if is_instance_valid(control) and control.is_visible_in_tree():
			var r := control.get_global_rect()
			labels.append([item[0], _round_box([r.position.x, r.position.y, r.end.x, r.end.y])])
	var line := {
		"frame": frame,
		"tick": tick if _has_clock else null,
		"entities": entities,
		"labels": labels,
	}
	_annotations.store_line(JSON.stringify(line, "", true, true))
	_annotations.flush()


## [bbox or null, visible] for a tracked node: its world AABB corners projected through the
## current camera and clipped to the viewport. bbox is null when the node is hidden, entirely
## behind the camera or off screen.
func _screen_box(node: Variant) -> Array:
	if not is_instance_valid(node) or not node.is_inside_tree():
		return [null, false]
	var camera := get_viewport().get_camera_3d()
	if camera == null or not node.is_visible_in_tree():
		return [null, false]
	var aabb := _world_aabb(node)
	var size := get_viewport().get_visible_rect().size
	var x0 := INF
	var y0 := INF
	var x1 := -INF
	var y1 := -INF
	var any := false
	for i in 8:
		var corner := aabb.get_endpoint(i)
		if camera.is_position_behind(corner):
			continue
		var p := camera.unproject_position(corner)
		x0 = minf(x0, p.x)
		y0 = minf(y0, p.y)
		x1 = maxf(x1, p.x)
		y1 = maxf(y1, p.y)
		any = true
	if not any:
		return [null, false]
	x0 = clampf(x0, 0.0, size.x)
	x1 = clampf(x1, 0.0, size.x)
	y0 = clampf(y0, 0.0, size.y)
	y1 = clampf(y1, 0.0, size.y)
	if x1 <= x0 or y1 <= y0:
		return [null, false]
	return [_round_box([x0, y0, x1, y1]), true]


func _world_aabb(node: Node3D) -> AABB:
	var result := AABB()
	var found := false
	var stack: Array[Node] = [node]
	while not stack.is_empty():
		var n: Node = stack.pop_back()
		if n is GeometryInstance3D and (n as GeometryInstance3D).is_visible_in_tree():
			var g := n as GeometryInstance3D
			var box := g.global_transform * g.get_aabb()
			result = box if not found else result.merge(box)
			found = true
		stack.append_array(n.get_children())
	return result if found else AABB(node.global_position, Vector3.ZERO)


func _round_box(values: Array) -> Array:
	return values.map(func(v: float) -> float: return roundf(v * 100.0) / 100.0)
