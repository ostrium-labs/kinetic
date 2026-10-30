"""Manim runtime: the code that runs inside the Manim subprocess (docs/runtime-api.md).

`kinetic.py` samples the trace and writes annotations (no Manim import), `scene.py` holds
`KineticScene`, the base class of every Manim shot scene, and `run.py` is the entry point the
adapter starts: `python -m ostrium_kinetic.manim_runtime.run --kinetic-job <run_dir>/job.json`.

Unlike the Godot and Blender runtimes this one runs in Kinetic's own Python environment
(Manim is MIT-licensed and Python-native), so it reuses `ostrium_kinetic.sim.trace` as its
sampler instead of re-implementing it. Importing this package does not import Manim.
"""
