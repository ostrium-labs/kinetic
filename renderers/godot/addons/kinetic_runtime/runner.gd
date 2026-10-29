## Main scene of the runtime project: instances the shot scene named by the job, makes the
## job's camera current and quits after frame_count frames. Failures go through Kinetic.fail,
## which writes runtime_error.txt and exits non-zero.
extends Node

const EXIT_NO_SCENE := 5
const EXIT_NO_CAMERA := 3


func _ready() -> void:
	if Kinetic.error != "":
		Kinetic.fail(Kinetic.error, Kinetic.error_code)
		return
	if not Kinetic.active:
		Kinetic.fail("no job: pass -- --kinetic-job <path to job.json>", Kinetic.EXIT_BAD_JOB)
		return
	var scene_path := "res://workspace/" + str(Kinetic.job["scene"])
	if not ResourceLoader.exists(scene_path):
		Kinetic.fail("shot scene not found: " + scene_path, EXIT_NO_SCENE)
		return
	var packed := load(scene_path) as PackedScene
	if packed == null:
		Kinetic.fail("cannot load shot scene: " + scene_path, EXIT_NO_SCENE)
		return
	var shot := packed.instantiate()
	if not shot is Node3D:
		Kinetic.fail("shot scene root must be a Node3D: " + scene_path, EXIT_NO_SCENE)
		if shot != null:
			shot.free()
		return
	add_child(shot)
	var camera_name := str(Kinetic.job["camera"])
	var camera := shot.find_child(camera_name, true, false) as Camera3D
	if camera == null:
		Kinetic.fail("shot scene has no Camera3D named '%s'" % camera_name, EXIT_NO_CAMERA)
		return
	camera.make_current()


func _process(_delta: float) -> void:
	# Quitting during the process step of the last frame still draws and writes that frame.
	if Kinetic.active and Kinetic.error == "" and Kinetic.frame >= Kinetic.frame_count - 1:
		get_tree().quit(0)
