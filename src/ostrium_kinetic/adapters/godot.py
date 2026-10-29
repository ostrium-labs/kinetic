"""Godot 4.7 adapter: a per-run project, a Movie Maker run, and a strict check of its output.

The runtime project (`renderers/godot/`) is copied into `<run_dir>/project/` with the film's
scene directory under `res://workspace/`, and its [display] section is rewritten so the
output size comes from the viewport (ADR 0005). Godot is never run with --headless: Movie
Maker needs a window and a GPU context.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath

from ostrium_kinetic import canonical
from ostrium_kinetic.adapters.process import ProcessResult, log_tail, run_logged
from ostrium_kinetic.env.hardware import png_size
from ostrium_kinetic.env.tools import discover, probe_version, resolve
from ostrium_kinetic.job import (
    ANNOTATIONS_FILE,
    FRAMES_DIR,
    JOB_FILE,
    LOG_FILE,
    AdapterError,
    FrameAnnotation,
    ShotJob,
    ShotResult,
    frame_name,
)
from ostrium_kinetic.workspace import Workspace

RUNTIME_DIR = Path(__file__).resolve().parents[3] / "renderers" / "godot"
PROJECT_DIR = "project"  # inside the run directory
WORKSPACE_DIR = "workspace"  # inside the project: res://workspace/<scene path>
ERROR_FILE = "runtime_error.txt"
MOVIE_FILE = "frame.png"  # Movie Maker writes frame00000000.png, ... next to it
IGNORED_DIRS = frozenset({".godot", "__pycache__"})
MAX_WINDOW = 640  # the visible window; output size comes from the viewport
MSAA_3D = {"draft": 0, "high": 2}  # 0 off, 2 = 4x
LOG_TAIL_LINES = 20

SettingValue = str | int | float | bool


def _files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*")
        if p.is_file() and not IGNORED_DIRS.intersection(p.relative_to(root).parts)
    ]


def runtime_sha256(root: Path = RUNTIME_DIR) -> str:
    """Hash of the runtime project, ignoring Godot's .godot/ import cache."""
    return canonical.sha256_tree(root, _files(root))


def window_size(width: int, height: int, limit: int = MAX_WINDOW) -> tuple[int, int]:
    """The visible window: the output size scaled to fit a limit x limit box, never enlarged."""
    scale = min(1.0, limit / width, limit / height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def godot_value(value: SettingValue) -> str:
    """A value in project.godot (ConfigFile) syntax."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    return canonical.dumps(value)


def set_project_settings(
    text: str,
    settings: Mapping[str, Mapping[str, SettingValue]],
    replace: Sequence[str] = (),
) -> str:
    """Set keys in project.godot sections. Sections named in `replace` are rewritten with
    exactly the given keys; other sections keep their keys and gain or update the given ones.
    Missing sections are appended."""
    preamble: list[str] = []
    sections: dict[str, list[str]] = {}
    current: list[str] = preamble
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = sections.setdefault(stripped[1:-1], [])
        else:
            current.append(line)
    for name, values in settings.items():
        body = [] if name in replace else sections.get(name, [])
        for key, value in values.items():
            entry = f"{key}={godot_value(value)}"
            index = next((i for i, ln in enumerate(body) if ln.startswith(f"{key}=")), None)
            if index is None:
                body = [*_strip_blank(body), entry]
            else:
                body[index] = entry
        sections[name] = body
    out = _strip_blank(preamble)
    for name, body in sections.items():
        out += ["", f"[{name}]", "", *_strip_blank(body)]
    return "\n".join(out).lstrip("\n") + "\n"


def _strip_blank(lines: list[str]) -> list[str]:
    start, end = 0, len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def job_settings(job: ShotJob) -> dict[str, dict[str, SettingValue]]:
    win_w, win_h = window_size(job.width, job.height)
    return {
        "display": {
            "window/size/viewport_width": job.width,
            "window/size/viewport_height": job.height,
            "window/size/window_width_override": win_w,
            "window/size/window_height_override": win_h,
            "window/stretch/mode": "viewport",
            "window/vsync/vsync_mode": 0,
        },
        "rendering": {
            "renderer/rendering_method": "forward_plus",
            "anti_aliasing/quality/msaa_3d": MSAA_3D[job.quality],
            "anti_aliasing/quality/screen_space_aa": 0,
            "anti_aliasing/quality/use_taa": False,
        },
    }


def scene_dir(job: ShotJob) -> PurePosixPath:
    """The scene's directory relative to the film; it is copied whole into the project."""
    parent = PurePosixPath(job.scene).parent
    if parent == PurePosixPath(".") or ".." in parent.parts or parent.is_absolute():
        raise AdapterError(
            f"scene {job.scene!r} must be in a subdirectory of the film (e.g. scenes/godot/)"
        )
    return parent


def build_project(job: ShotJob, run_dir: Path, runtime_dir: Path = RUNTIME_DIR) -> Path:
    """Create run_dir/project: the runtime, the film's scene directory under workspace/, and
    project.godot configured for the job. Returns the project directory."""
    if not (runtime_dir / "project.godot").is_file():
        raise AdapterError(f"Godot runtime project not found at {runtime_dir}")
    film_dir = Path(job.film_dir)
    rel = scene_dir(job)
    if not (film_dir / job.scene).is_file():
        raise AdapterError(f"scene not found: {film_dir / job.scene}")
    project = run_dir / PROJECT_DIR
    if project.exists():
        shutil.rmtree(project)
    ignore = shutil.ignore_patterns(*IGNORED_DIRS)
    shutil.copytree(runtime_dir, project, ignore=ignore)
    shutil.copytree(film_dir / rel, project / WORKSPACE_DIR / rel, ignore=ignore)
    config = project / "project.godot"
    text = config.read_text("utf-8")
    canonical.write_text_lf(
        config, set_project_settings(text, job_settings(job), replace=["display"])
    )
    return project


def godot_command(exe: Path | str, project: Path, run_dir: Path, job: ShotJob) -> list[str]:
    return [
        str(exe),
        "--path",
        str(project),
        "--rendering-method",
        "forward_plus",
        "--write-movie",
        str(run_dir / FRAMES_DIR / MOVIE_FILE),
        "--fixed-fps",
        str(job.fps_int),
        "--quit-after",
        str(job.frame_count),
        "--",
        "--kinetic-job",
        str(run_dir / JOB_FILE),
    ]


def render_timeout(frame_count: int) -> float:
    """Generous enough for a cold shader cache (about 3x slower than warm in M0)."""
    return 120.0 + 2.0 * frame_count


def check_output(job: ShotJob, run_dir: Path, result: ProcessResult) -> None:
    """Raise AdapterError unless the run produced exactly the frames (and annotations) the
    job asked for. An exit code of 0 alone is never success."""

    def fail(message: str) -> AdapterError:
        tail = result.tail(LOG_TAIL_LINES)
        return AdapterError(f"{message}\n--- {run_dir / LOG_FILE} (tail) ---\n{tail}")

    if result.timed_out:
        raise fail(f"Godot timed out after {result.seconds:.0f} s")
    error_file = run_dir / ERROR_FILE
    if error_file.is_file():
        reason = error_file.read_text("utf-8", errors="replace").strip()
        raise fail(f"Godot runtime error (exit {result.returncode}): {reason}")
    if result.returncode != 0:
        raise fail(f"Godot exited with code {result.returncode}")
    log = log_tail(result.log_path, lines=1_000_000)
    script_errors = [ln for ln in log.splitlines() if "SCRIPT ERROR" in ln]
    if script_errors:
        raise fail(f"GDScript errors during the render: {script_errors[0].strip()}")

    frames_dir = run_dir / FRAMES_DIR
    expected = [frame_name(i) for i in range(job.frame_count)]
    found = sorted(p.name for p in frames_dir.glob("*.png"))
    if found != expected:
        missing = sorted(set(expected) - set(found))
        extra = sorted(set(found) - set(expected))
        raise fail(
            f"expected {job.frame_count} frames, found {len(found)}"
            f" (missing {missing[:3]}, unexpected {extra[:3]})"
        )
    for name in expected:
        size = png_size(frames_dir / name)
        if size != (job.width, job.height):
            raise fail(f"{name} is {size}, expected {(job.width, job.height)}")

    if job.annotate:
        path = run_dir / ANNOTATIONS_FILE
        if not path.is_file():
            raise fail(f"{ANNOTATIONS_FILE} was not written")
        lines = [ln for ln in path.read_text("utf-8").splitlines() if ln.strip()]
        if len(lines) != job.frame_count:
            raise fail(f"{ANNOTATIONS_FILE} has {len(lines)} lines, expected {job.frame_count}")
        for index, line in enumerate(lines):
            try:
                frame = FrameAnnotation.model_validate_json(line).frame
            except ValueError as exc:
                raise fail(f"{ANNOTATIONS_FILE} line {index + 1} is invalid: {exc}") from exc
            if frame != index:
                raise fail(f"{ANNOTATIONS_FILE} line {index + 1} is for frame {frame}")


def render_key(job: ShotJob) -> str:
    """Hash of the job without its run directory. The orchestrator may replace it with the
    plan's cache key."""
    return canonical.sha256_json(job.model_dump(mode="json", exclude={"out_dir"}))


class GodotAdapter:
    name = "godot"

    def __init__(self, executable: Path | None = None, runtime_dir: Path = RUNTIME_DIR) -> None:
        self._executable = executable
        self.runtime_dir = runtime_dir
        self._versions: dict[str, str | None] = {}

    def executable(self, ws: Workspace) -> Path:
        if self._executable is not None:
            return self._executable
        found = resolve(discover("godot", ws, probe=False))
        if found is None:
            raise AdapterError(
                "Godot not found: set [tools] godot in kinetic.toml or KINETIC_GODOT"
                " (see `kinetic doctor`)"
            )
        return Path(found.path)

    def version(self, ws: Workspace) -> str | None:
        """Full Godot version, e.g. "4.7.2.stable.official.ed1daf0bf"."""
        try:
            exe = str(self.executable(ws))
        except AdapterError:
            return None
        if exe not in self._versions:
            version, banner, _ = probe_version("godot", exe)
            if version and banner and banner.split()[0].startswith(version):
                version = banner.split()[0]
            self._versions[exe] = version
        return self._versions[exe]

    def runtime_sha256(self) -> str:
        return runtime_sha256(self.runtime_dir)

    def render(self, job: ShotJob, run_dir: Path, ws: Workspace) -> ShotResult:
        if job.renderer != "godot":
            raise AdapterError(f"job is for {job.renderer}, not godot")
        run_dir = run_dir.resolve()
        if Path(job.out_dir).resolve() != run_dir:
            raise AdapterError(f"job.out_dir {job.out_dir} is not the run directory {run_dir}")
        exe = self.executable(ws)
        version = self.version(ws)

        frames_dir = run_dir / FRAMES_DIR
        if frames_dir.exists():
            shutil.rmtree(frames_dir)
        frames_dir.mkdir(parents=True)
        for stale in (ANNOTATIONS_FILE, ERROR_FILE):
            (run_dir / stale).unlink(missing_ok=True)
        project = build_project(job, run_dir, self.runtime_dir)
        canonical.write_text_lf(run_dir / JOB_FILE, job.model_dump_json(indent=2) + "\n")

        args = godot_command(exe, project, run_dir, job)
        try:
            result = run_logged(args, run_dir / LOG_FILE, render_timeout(job.frame_count))
        except OSError as exc:
            raise AdapterError(f"cannot start Godot ({exe}): {exc}") from exc
        for wav in frames_dir.glob("*.wav"):  # Movie Maker always writes the audio track
            wav.unlink()
        check_output(job, run_dir, result)
        return ShotResult(
            shot_id=job.shot_id,
            render_key=render_key(job),
            renderer=self.name,
            renderer_version=version,
            status="frames_ok",
            frame_count=job.frame_count,
            width=job.width,
            height=job.height,
            seconds=round(result.seconds, 3),
        )
