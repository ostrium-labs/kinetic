## Headless parity tool: samples a trace at the given ticks and prints one JSON line,
## prefixed with "KINETIC_SAMPLE ", of
## {"<tick>": {"state": {...}, "spans": [...], "last": {"<entity>": last_span view}}}.
## Span views carry progress and planned_progress (and planned_end_tick when set); "last"
## lists every entity with a span that has ended by that tick.
##
## godot --headless --path <project> --script res://addons/kinetic_runtime/tools/sample_trace.gd \
##     -- --trace <trace dir> --ticks 0,10,1200
extends SceneTree

const KineticTrace := preload("../trace.gd")


func _initialize() -> void:
	var args := OS.get_cmdline_user_args()
	var trace_dir := ""
	var ticks: Array[int] = []
	var i := 0
	while i < args.size():
		if args[i] == "--trace" and i + 1 < args.size():
			trace_dir = args[i + 1]
			i += 1
		elif args[i] == "--ticks" and i + 1 < args.size():
			for part in args[i + 1].split(",", false):
				ticks.append(part.to_int())
			i += 1
		i += 1
	if trace_dir == "":
		printerr("usage: -- --trace <dir> --ticks t0,t1,...")
		quit(2)
		return
	var trace := KineticTrace.new()
	if not trace.load_dir(trace_dir):
		printerr("trace: ", trace.error)
		quit(4)
		return
	var entities := {}
	for span: Dictionary in trace.spans:
		entities[span["entity"]] = true
	var out := {}
	for t in ticks:
		var last := {}
		for entity: String in entities:
			var view := trace.last_span(entity, t)
			if not view.is_empty():
				last[entity] = view
		out[str(t)] = {"state": trace.state_at(t), "spans": trace.spans_active(t), "last": last}
	print("KINETIC_SAMPLE " + JSON.stringify(out, "", true, true))
	quit(0)
