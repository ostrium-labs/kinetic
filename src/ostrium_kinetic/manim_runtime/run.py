# pyright: reportMissingImports=false, reportMissingModuleSource=false
"""Entry point of the Manim subprocess:

    python -m ostrium_kinetic.manim_runtime.run --kinetic-job <run_dir>/job.json

Loads the job and the trace, imports the shot scene module (`job.scene`, relative to
`job.film_dir`) and renders its `Shot` class with the Python API (`tempconfig` +
`Scene.render()`) as a PNG sequence into `<run_dir>/manim_media/`. The images are then
written as RGB to `<run_dir>/frames/frame%08d.png` (Manim writes RGBA) and the media directory
is removed. One annotation line per frame goes to `<run_dir>/annotations.jsonl`.

Exit codes (each also writes runtime_error.txt): 1 scene raised, 2 bad job, 3 font missing,
4 trace unreadable, 5 scene module or Shot class missing, 6 wrong number of frames,
7 manim not importable.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import shutil
import sys
import traceback
from collections.abc import Sequence
from fractions import Fraction
from pathlib import Path
from typing import Any

from ostrium_kinetic.manim_runtime.kinetic import RuntimeFailure as Failure

ERROR_FILE = "runtime_error.txt"
FRAMES_DIR = "frames"
FRAME_PATTERN = "frame%08d.png"
ANNOTATIONS_FILE = "annotations.jsonl"
MEDIA_DIR = "manim_media"
FRAME_HEIGHT_UNITS = 8.0  # Manim's default frame height; width follows the aspect ratio
SHOT_CLASS = "Shot"


def _job_path(argv: Sequence[str]) -> Path:
    parser = argparse.ArgumentParser(prog="kinetic-manim-runtime")
    parser.add_argument("--kinetic-job", required=True, type=Path)
    return parser.parse_args(list(argv)).kinetic_job


def _load_scene_class(film_dir: Path, scene: str) -> type:
    if importlib.util.find_spec("manim") is None:
        raise Failure(
            f"manim is not importable in {sys.executable}: install with `uv sync --extra manim`",
            7,
        )
    from ostrium_kinetic.manim_runtime.scene import KineticScene

    path = film_dir / scene
    if path.suffix != ".py" or not path.is_file():
        raise Failure(f"scene module not found (a .py file): {path}", 5)
    sys.path.insert(0, str(path.parent))  # scenes may import helpers from their directory
    spec = importlib.util.spec_from_file_location(f"kinetic_shot_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise Failure(f"cannot import scene module {path}", 5)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    cls = getattr(module, SHOT_CLASS, None)
    if not (isinstance(cls, type) and issubclass(cls, KineticScene)):
        raise Failure(f"{path} must define `class {SHOT_CLASS}(KineticScene)`", 5)
    return cls


def render(job_file: Path) -> None:
    from ostrium_kinetic.manim_runtime.kinetic import Kinetic, annotation_line, load_job
    from ostrium_kinetic.sim.trace import Trace

    try:
        job = load_job(job_file)
        out_dir = Path(job["out_dir"])
        width, height, frame_count = int(job["width"]), int(job["height"]), int(job["frame_count"])
        fps = Fraction(str(job["fps"]))
        film_dir = Path(job["film_dir"])
        scene = str(job["scene"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Failure(f"bad job {job_file}: {exc}", 2) from exc
    if str(job.get("contract")) != "0.1":
        raise Failure(f"unsupported job contract {job.get('contract')!r}", 2)
    if fps.denominator != 1:
        raise Failure(f"Manim runtime needs an integer frame rate, got {fps}", 2)
    if frame_count <= 0:
        raise Failure("job frame_count must be positive", 2)

    trace = None
    if job.get("trace_dir"):
        try:
            trace = Trace.load(Path(job["trace_dir"]))
        except (OSError, ValueError) as exc:
            raise Failure(f"trace: {exc}", 4) from exc

    frame_width = FRAME_HEIGHT_UNITS * width / height
    try:
        k = Kinetic(job, trace, frame_width, FRAME_HEIGHT_UNITS)
    except ValueError as exc:
        raise Failure(str(exc), 2) from exc
    shot_cls = _load_scene_class(film_dir, scene)

    from manim import tempconfig

    media = out_dir / MEDIA_DIR
    if media.exists():
        shutil.rmtree(media)
    options: dict[str, Any] = {
        "format": "png_sequence",
        "disable_caching": True,
        "media_dir": str(media),
        "pixel_width": width,
        "pixel_height": height,
        "frame_height": FRAME_HEIGHT_UNITS,
        "frame_width": frame_width,
        "frame_rate": int(fps),
        "background_color": str(k.style("background", "#0E1116")),
        "verbosity": "WARNING",
        "progress_bar": "none",
        "seed": int(job.get("seed", 0)),
    }
    annotate = bool(job.get("annotate", True))
    lines: list[str] = []
    with tempconfig(options):
        scene_obj = shot_cls()
        scene_obj.k = k
        if annotate:
            scene_obj.on_frame = lambda record: lines.append(annotation_line(record))
        scene_obj.render()
        writer = scene_obj.renderer.file_writer
        if writer.frame_count != frame_count:
            raise Failure(f"Manim wrote {writer.frame_count} images, expected {frame_count}", 6)
        sources = [Path(writer.output_plan.image_frame_path(i)) for i in range(frame_count)]

    _write_frames(sources, out_dir / FRAMES_DIR, (width, height))
    shutil.rmtree(media, ignore_errors=True)
    if annotate:
        (out_dir / ANNOTATIONS_FILE).write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8", newline="\n"
        )


def _write_frames(sources: list[Path], frames_dir: Path, size: tuple[int, int]) -> None:
    """Manim's RGBA images -> RGB frame%08d.png (opaque background, alpha dropped)."""
    from PIL import Image

    frames_dir.mkdir(parents=True, exist_ok=True)
    for index, src in enumerate(sources):
        if not src.is_file():
            raise Failure(f"Manim image for frame {index} is missing: {src}", 6)
        with Image.open(src) as image:
            if image.size != size:
                raise Failure(f"{src.name} is {image.size}, expected {size}", 6)
            image.convert("RGB").save(frames_dir / (FRAME_PATTERN % index), compress_level=6)
    extra = sorted(p.name for p in sources[0].parent.glob("*.png")) if sources else []
    if len(extra) != len(sources):
        raise Failure(f"Manim wrote {len(extra)} images, expected {len(sources)}", 6)


def _write_error(job_file: Path, message: str) -> None:
    """runtime_error.txt next to job.json (the adapter writes the job into the run directory)."""
    with contextlib.suppress(OSError):
        (job_file.parent / ERROR_FILE).write_text(message + "\n", encoding="utf-8", newline="\n")


def main(argv: Sequence[str] | None = None) -> int:
    job_file = _job_path(sys.argv[1:] if argv is None else argv)
    try:
        render(job_file)
    except Failure as exc:
        message, code = str(exc), exc.code
    except Exception as exc:
        traceback.print_exc()
        tb = traceback.extract_tb(exc.__traceback__)
        where = f" at {Path(tb[-1].filename).name}:{tb[-1].lineno}" if tb else ""
        message, code = f"{type(exc).__name__}{where}: {exc}", 1
    else:
        return 0
    print(f"kinetic: {message}", file=sys.stderr)
    _write_error(job_file, message)
    return code


if __name__ == "__main__":
    sys.exit(main())
