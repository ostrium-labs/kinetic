## Headless parity tool: samples a trace at the given ticks and prints one JSON line,
## prefixed with "KINETIC_SAMPLE ", of {"<tick>": {"state": {...}, "spans": [...]}}.
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
	var out := {}
	for t in ticks:
		out[str(t)] = {"state": trace.state_at(t), "spans": trace.spans_active(t)}
	print("KINETIC_SAMPLE " + JSON.stringify(out, "", true, true))
	quit(0)
