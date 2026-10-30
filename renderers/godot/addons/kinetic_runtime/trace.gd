## Trace sampler. Must match the reference in src/ostrium_kinetic/sim/trace.py exactly:
##   state_at(t)     fold trace.jsonl events with event.tick <= t
##   spans_active(t) spans with begin_tick <= t < end_tick, with progress by interp
##   planned progress over [begin_tick, planned_end_tick] when a span has a planned end
## State is precomputed once per distinct event tick, so sampling a frame is a binary search.
## Returned dictionaries are shared snapshots: callers must not modify them.
extends RefCounted

const JsonLite := preload("json_lite.gd")

var meta: Dictionary = {}
var events: Array = []
var spans: Array = []
## Seconds per tick as [numerator, denominator].
var tick_rate: Array = [0, 1]
var error := ""

var _snap_ticks := PackedInt64Array()
var _snaps: Array = []


## Load trace.meta.json, trace.jsonl and spans.jsonl from `dir`. False (and `error`) on failure.
func load_dir(dir: String) -> bool:
	var m: Variant = _read_json(dir.path_join("trace.meta.json"))
	if not m is Dictionary:
		return _failed("cannot read %s: %s" % [dir.path_join("trace.meta.json"), error])
	meta = m
	if str(meta.get("contract", "")) != "0.1":
		return _failed("unsupported trace contract %s" % meta.get("contract"))
	var rate: Variant = parse_rational(meta.get("tick_rate"))
	if rate == null:
		return _failed("bad tick_rate %s" % meta.get("tick_rate"))
	tick_rate = rate
	var ev: Variant = _read_jsonl(dir.path_join("trace.jsonl"))
	var sp: Variant = _read_jsonl(dir.path_join("spans.jsonl"))
	if ev == null or sp == null:
		return false
	events = ev
	spans = sp
	if events.size() != int(meta.get("event_count", -1)):
		return _failed("event count disagrees with trace.meta.json")
	if spans.size() != int(meta.get("span_count", -1)):
		return _failed("span count disagrees with trace.meta.json")
	for span: Dictionary in spans:
		var keys: Array = []
		var raw_keys: Variant = span.get("keys")
		for pair: Array in raw_keys if raw_keys is Array else []:
			var u: Variant = parse_rational(pair[0])
			var p: Variant = parse_rational(pair[1])
			if u == null or p == null:
				return _failed("span %s: bad curve key %s" % [span.get("span_id"), pair])
			keys.append([u, p])
		span["_keys"] = keys
		if not span.has("attrs") or span["attrs"] == null:
			span["attrs"] = {}
	return _build_snapshots()


## Parse an exact rational: int, "a", "a/b" or a decimal string. Returns [num, den] or null.
static func parse_rational(value: Variant) -> Variant:
	if value is int:
		return [value, 1]
	if not value is String:
		return null
	var text: String = value.strip_edges()
	if text.contains("/"):
		var parts := text.split("/")
		if parts.size() != 2 or not parts[0].is_valid_int() or not parts[1].is_valid_int():
			return null
		var den := parts[1].to_int()
		return null if den <= 0 else [parts[0].to_int(), den]
	if text.is_valid_int():
		return [text.to_int(), 1]
	if text.is_valid_float() and text.contains("."):
		var neg := text.begins_with("-")
		var body := text.trim_prefix("-").trim_prefix("+")
		var dot := body.find(".")
		var digits := body.substr(0, dot) + body.substr(dot + 1)
		if not digits.is_valid_int():
			return null
		var den := int(pow(10, body.length() - dot - 1))
		return [-digits.to_int() if neg else digits.to_int(), den]
	return null


func seconds(tick: int) -> float:
	return float(tick * tick_rate[0]) / float(tick_rate[1])


## State of every entity alive at `tick`: {entity: {field: value}}.
func state_at(tick: int) -> Dictionary:
	var i := _snap_ticks.bsearch(tick, false)
	return {} if i == 0 else _snaps[i - 1]


## Active spans at `tick`, as span views (see span_view) with "entity" added.
func spans_active(tick: int) -> Array:
	var out := []
	for span: Dictionary in spans:
		if int(span["begin_tick"]) <= tick and tick < int(span["end_tick"]):
			var view := span_view(span, tick)
			view["entity"] = span["entity"]
			out.append(view)
	return out


## The entity's most recent span that has ended by `tick` (end_tick <= tick), as a span
## view with progress 1.0 and planned_progress where it stopped (short of 1.0 for a span cut
## before its planned end), or {} if none. Lets a scene show where an entity ended up
## (delivered, lost) as a pure function of the tick.
func last_span(entity: String, tick: int) -> Dictionary:
	var best: Dictionary = {}
	for span: Dictionary in spans:
		if span["entity"] != entity or int(span["end_tick"]) > tick:
			continue
		if best.is_empty() or int(span["end_tick"]) >= int(best["end_tick"]):
			best = span
	if best.is_empty():
		return {}
	var view := span_view(best, int(best["end_tick"]))
	view["progress"] = 1.0
	return view


## {span_id, kind, interp, outcome, begin_tick, end_tick, progress, planned_progress} plus
## planned_end_tick when the span has one, plus the span's attrs. Span fields win over attrs
## with the same name.
func span_view(span: Dictionary, tick: int) -> Dictionary:
	var view: Dictionary = span["attrs"].duplicate()
	for key in ["span_id", "kind", "interp", "outcome", "begin_tick", "end_tick"]:
		view[key] = span[key]
	if span.get("planned_end_tick") != null:
		view["planned_end_tick"] = span["planned_end_tick"]
	view["progress"] = progress(span, tick)
	view["planned_progress"] = planned_progress(span, tick)
	return view


## Progress of an active span at `tick`: step -> 0, linear -> u, curve:<name> -> keyed.
func progress(span: Dictionary, tick: int) -> float:
	return _interp_at(span, tick, int(span["end_tick"]))


## Progress over the planned interval [begin_tick, planned_end_tick]; equals progress() when
## the span has no planned end. Use it to place an entity on a path that may be cut short.
func planned_progress(span: Dictionary, tick: int) -> float:
	var planned: Variant = span.get("planned_end_tick")
	return _interp_at(span, tick, int(span["end_tick"]) if planned == null else int(planned))


func _interp_at(span: Dictionary, tick: int, end: int) -> float:
	var interp: String = span["interp"]
	if interp == "step":
		return 0.0
	var num: int = tick - int(span["begin_tick"])
	var den: int = end - int(span["begin_tick"])
	if interp == "linear":
		return float(num) / float(den)
	var keys: Array = span["_keys"]
	for k in range(keys.size() - 1):
		var u0: Array = keys[k][0]
		var u1: Array = keys[k + 1][0]
		# u0 <= u <= u1, exactly (all denominators are positive).
		if u0[0] * den <= num * u0[1] and num * u1[1] <= u1[0] * den:
			var p0: Array = keys[k][1]
			var p1: Array = keys[k + 1][1]
			var fu := float(num) / float(den)
			var fu0 := float(u0[0]) / float(u0[1])
			var fu1 := float(u1[0]) / float(u1[1])
			var fp0 := float(p0[0]) / float(p0[1])
			var fp1 := float(p1[0]) / float(p1[1])
			return fp0 + (fp1 - fp0) * (fu - fu0) / (fu1 - fu0)
	push_error("span %s: u=%d/%d outside curve keys" % [span["span_id"], num, den])
	return NAN


## `signal` events with from_tick < tick <= to_tick: {tick, entity, name, attrs}.
func signals_in(from_tick: int, to_tick: int) -> Array:
	var out := []
	for e: Dictionary in events:
		var t: int = e["tick"]
		if t > to_tick:
			break
		if t > from_tick and e["type"] == "signal":
			var attrs: Variant = e.get("attrs")
			out.append(
				{
					"tick": t,
					"entity": e["entity"],
					"name": e.get("name"),
					"attrs": attrs if attrs != null else {},
				}
			)
	return out


func _build_snapshots() -> bool:
	var states := {}
	var n := events.size()
	for i in n:
		var e: Dictionary = events[i]
		var entity: String = e["entity"]
		var fields: Variant = e.get("state")
		match e["type"]:
			"entity.created":
				states[entity] = fields.duplicate() if fields is Dictionary else {}
			"state.set":
				if not states.has(entity):
					return _failed("state.set on unknown entity %s at tick %d" % [entity, e["tick"]])
				if fields is Dictionary:
					states[entity].merge(fields, true)
			"entity.destroyed":
				if not states.has(entity):
					return _failed("entity.destroyed on unknown entity %s" % entity)
				states.erase(entity)
		if i == n - 1 or int(events[i + 1]["tick"]) != int(e["tick"]):
			_snap_ticks.append(int(e["tick"]))
			_snaps.append(states.duplicate(true))
	return true


func _failed(message: String) -> bool:
	error = message
	return false


func _read_json(path: String) -> Variant:
	var text := FileAccess.get_file_as_string(path)
	if text == "" and FileAccess.get_open_error() != OK:
		error = error_string(FileAccess.get_open_error())
		return null
	var reader := JsonLite.new()
	var value: Variant = reader.parse(text)
	if reader.error != "":
		error = reader.error
		return null
	return value


func _read_jsonl(path: String) -> Variant:
	var text := FileAccess.get_file_as_string(path)
	if text == "" and FileAccess.get_open_error() != OK:
		_failed("cannot read %s: %s" % [path, error_string(FileAccess.get_open_error())])
		return null
	var reader := JsonLite.new()
	var out := []
	var line_no := 0
	for line in text.split("\n"):
		line_no += 1
		if line.strip_edges() == "":
			continue
		var value: Variant = reader.parse(line)
		if reader.error != "" or not value is Dictionary:
			_failed("%s:%d: %s" % [path, line_no, reader.error if reader.error else "not an object"])
			return null
		out.append(value)
	return out
