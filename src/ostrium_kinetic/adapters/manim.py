"""Manim CE adapter: a subprocess of Kinetic's own Python with the `manim` extra installed,
running `ostrium_kinetic.manim_runtime.run`, and a strict check of what it wrote.

Manim renders on the CPU (Cairo), so no GPU lock is taken. The interpreter defaults to the
one running Kinetic (`sys.executable`); if `import manim` fails there, rendering stops with an
AdapterError that says how to install it (`uv sync --extra manim`).
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from ostrium_kinetic import canonical, proc
from ostrium_kinetic.adapters.godot import ProcessResult, render_key, run_logged
from ostrium_kinetic.env.hardware import png_size
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

RUNTIME_DIR = Path(__file__).resolve().parents[1] / "manim_runtime"
RUNTIME_MODULE = "ostrium_kinetic.manim_runtime.run"
ERROR_FILE = "runtime_error.txt"
MEDIA_DIR = "manim_media"
LOG_TAIL_LINES = 20
PROBE_TIMEOUT_S = 120.0  # importing manim takes several seconds on a cold disk cache
INSTALL_HINT = "install with `uv sync --extra manim`"
_PROBE = "import manim, sys; sys.stdout.write('KINETIC_MANIM ' + manim.__version__)"


def runtime_sha256(root: Path = RUNTIME_DIR) -> str:
    """Hash of the runtime's Python sources (manim_runtime/*.py)."""
    return canonical.sha256_tree(root, sorted(root.glob("*.py")))


def manim_command(python: Sequence[str], run_dir: Path) -> list[str]:
    return [*python, "-m", RUNTIME_MODULE, "--kinetic-job", str(run_dir / JOB_FILE)]


def render_timeout(frame_count: int, width: int, height: int) -> float:
    """Cairo time scales with pixels: M0 measured about 0.2-0.3 s per 1080p frame including
    text shaping; allow 4x that plus process start (importing manim)."""
    pixels = width * height / (1920 * 1080)
    return 120.0 + 1.0 * frame_count * max(pixels, 0.25)


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # pydub (a manim dependency) emits SyntaxWarnings on first compile; keep render.log clean.
    env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"
    return env


def check_output(job: ShotJob, run_dir: Path, result: ProcessResult) -> None:
    """Raise AdapterError unless the run produced exactly the frames (and annotations) the
    job asked for. An exit code of 0 alone is never success."""

    def fail(message: str) -> AdapterError:
        tail = result.tail(LOG_TAIL_LINES)
        return AdapterError(f"{message}\n--- {run_dir / LOG_FILE} (tail) ---\n{tail}")

    if result.timed_out:
        raise fail(f"Manim timed out after {result.seconds:.0f} s")
    error_file = run_dir / ERROR_FILE
    if error_file.is_file():
        reason = error_file.read_text("utf-8", errors="replace").strip()
        raise fail(f"Manim runtime error (exit {result.returncode}): {reason}")
    if result.returncode != 0:
        raise fail(f"Manim runtime exited with code {result.returncode}")
    if (run_dir / MEDIA_DIR).exists():
        raise fail(f"{MEDIA_DIR}/ was left behind: the runtime did not finish")

    frames_dir = run_dir / FRAMES_DIR
    expected = [frame_name(i) for i in range(job.frame_count)]
    found = sorted(p.name for p in frames_dir.glob("*.png")) if frames_dir.is_dir() else []
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
                ann = FrameAnnotation.model_validate_json(line)
            except ValueError as exc:
                raise fail(f"{ANNOTATIONS_FILE} line {index + 1} is invalid: {exc}") from exc
            if ann.frame != index:
                raise fail(f"{ANNOTATIONS_FILE} line {index + 1} is for frame {ann.frame}")
            if ann.tick != job.tick_at(index):
                raise fail(
                    f"{ANNOTATIONS_FILE} frame {index}: tick {ann.tick},"
                    f" expected {job.tick_at(index)}"
                )


class ManimAdapter:
    name = "manim"

    def __init__(self, python: Sequence[str] | None = None, runtime_dir: Path = RUNTIME_DIR):
        """`python`: the interpreter command (with any leading arguments) that runs the
        runtime; it must be able to import both `manim` and `ostrium_kinetic`."""
        self.python: list[str] = list(python) if python else [sys.executable]
        self.runtime_dir = runtime_dir
        self._probe: tuple[str | None, str] | None = None

    def probe(self) -> tuple[str | None, str]:
        """(manim version or None, diagnostic) for this adapter's interpreter; cached."""
        if self._probe is None:
            try:
                done = proc.run([*self.python, "-c", _PROBE], PROBE_TIMEOUT_S, env=_env())
            except OSError as exc:
                self._probe = (None, f"cannot start {self.python[0]}: {exc}")
            else:
                out = done.stdout.strip().splitlines()
                last = out[-1] if out else ""
                if done.returncode == 0 and last.startswith("KINETIC_MANIM "):
                    self._probe = (last.split(" ", 1)[1].strip(), "")
                else:
                    detail = log_tail_text(done.stderr or done.stdout, 3)
                    if done.timed_out:
                        detail = f"timed out after {PROBE_TIMEOUT_S:g} s"
                    self._probe = (None, detail)
        return self._probe

    def version(self, ws: Workspace) -> str | None:
        """Manim version importable by the runtime interpreter, e.g. "0.22.1"."""
        return self.probe()[0]

    def runtime_sha256(self) -> str:
        return runtime_sha256(self.runtime_dir)

    def require(self) -> str:
        version, detail = self.probe()
        if version is None:
            interpreter = " ".join(self.python)
            raise AdapterError(
                f"Manim is not importable by {interpreter}: {INSTALL_HINT}"
                + (f"\n{detail}" if detail else "")
            )
        return version

    def render(self, job: ShotJob, run_dir: Path, ws: Workspace) -> ShotResult:
        if job.renderer != "manim":
            raise AdapterError(f"job is for {job.renderer}, not manim")
        run_dir = run_dir.resolve()
        if Path(job.out_dir).resolve() != run_dir:
            raise AdapterError(f"job.out_dir {job.out_dir} is not the run directory {run_dir}")
        scene = Path(job.film_dir) / job.scene
        if not scene.is_file():
            raise AdapterError(f"scene not found: {scene}")
        try:
            job.fps_int  # noqa: B018 - property that validates
        except ValueError as exc:
            raise AdapterError(str(exc)) from exc
        version = self.require()

        run_dir.mkdir(parents=True, exist_ok=True)
        for stale_dir in (FRAMES_DIR, MEDIA_DIR):
            if (run_dir / stale_dir).exists():
                shutil.rmtree(run_dir / stale_dir)
        for stale in (ANNOTATIONS_FILE, ERROR_FILE):
            (run_dir / stale).unlink(missing_ok=True)
        canonical.write_text_lf(run_dir / JOB_FILE, job.model_dump_json(indent=2) + "\n")

        args = manim_command(self.python, run_dir)
        timeout = render_timeout(job.frame_count, job.width, job.height)
        try:
            result = run_logged(args, run_dir / LOG_FILE, timeout, cwd=run_dir, env=_env())
        except OSError as exc:
            raise AdapterError(f"cannot start {self.python[0]}: {exc}") from exc
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


def log_tail_text(text: str, lines: int) -> str:
    kept = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])
