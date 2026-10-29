"""The boundary between the Kinetic core and a renderer process.

The core writes a `ShotJob` (job.json) into a shot's run directory. The renderer runtime
(GDScript, bpy or Manim) reads it, samples the trace at each frame's tick, writes frames as
`frames/frame%08d.png` and, when `annotate` is set, one `FrameAnnotation` per frame to
`annotations.jsonl`. The adapter records the outcome in `shot.json` (`ShotResult`).

Renderer runtimes never import this package; they read these files only.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ostrium_kinetic.canonical import Exact
from ostrium_kinetic.sim.model import Scalar
from ostrium_kinetic.workspace import Workspace

FRAMES_DIR = "frames"
FRAME_PATTERN = "frame%08d.png"  # ffmpeg-style; Godot's Movie Maker writes this for "frame.png"
JOB_FILE = "job.json"
ANNOTATIONS_FILE = "annotations.jsonl"
RESULT_FILE = "shot.json"
LOG_FILE = "render.log"
MEZZANINE_FILE = "mezzanine.mkv"
STILLS_DIR = "stills"


def frame_name(index: int) -> str:
    return FRAME_PATTERN % index


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShotJob(_Model):
    contract: Literal["0.1"] = "0.1"
    film_id: str
    shot_id: str
    renderer: Literal["godot", "blender", "manim"]
    film_dir: str  # absolute, POSIX separators
    scene: str  # relative to film_dir, POSIX separators
    fps: Exact
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    frame_count: int = Field(gt=0)
    framing: str  # "16:9" | "9:16"
    camera: str  # camera name the scene must provide for this framing
    quality: Literal["draft", "high"]
    seed: int = 0
    trace_dir: str | None = None  # absolute; contains trace.jsonl, spans.jsonl, trace.meta.json
    tick_rate: Exact | None = None  # seconds per tick
    tick_start: int | None = None
    tick_end: int | None = None
    evidence: str | None = None  # the shot's evidence class; the scene shows its badge
    time_label: str | None = None  # on-screen time-scale label when sim speed != 1
    variant_calm: bool = False  # reduced-motion variant: cuts instead of camera moves
    annotate: bool = True
    out_dir: str  # absolute run directory
    style: dict[str, Scalar] = Field(default_factory=dict)

    def tick_at(self, frame: int) -> int | None:
        """Same exact mapping as timeline.ShotClock; renderer runtimes must reproduce it:
        tick_start + ((tick_end - tick_start) * frame) // frame_count."""
        if self.tick_start is None or self.tick_end is None:
            return None
        if not 0 <= frame < self.frame_count:
            raise ValueError(f"frame {frame} outside shot of {self.frame_count} frames")
        return self.tick_start + ((self.tick_end - self.tick_start) * frame) // self.frame_count

    @property
    def fps_int(self) -> int:
        if Fraction(self.fps).denominator != 1:
            raise ValueError(f"renderer needs an integer frame rate, got {self.fps}")
        return int(self.fps)


class EntityAnnotation(_Model):
    """What the renderer drew for one tracked entity on one frame."""

    state: dict[str, Scalar] = Field(default_factory=dict)  # as the renderer read it
    active_span: dict[str, Scalar] | None = None  # span fields + attrs + "progress" (float)
    bbox: tuple[float, float, float, float] | None = None  # x0, y0, x1, y1 px, origin top-left
    visible: bool = False  # bbox intersects the frame and the node is visible


class FrameAnnotation(_Model):
    frame: int = Field(ge=0)
    tick: int | None = None
    entities: dict[str, EntityAnnotation] = Field(default_factory=dict)
    labels: list[tuple[str, tuple[float, float, float, float]]] = Field(default_factory=list)
    # (text, bbox) of on-screen text, for safe-area and minimum-size checks


ShotStatus = Literal["rendering", "frames_ok", "mezzanine_ok", "failed"]


class ShotResult(_Model):
    shot_id: str
    render_key: str
    renderer: str
    renderer_version: str | None = None
    status: ShotStatus
    frame_count: int
    width: int
    height: int
    seconds: float | None = None  # wall time of the render
    error: str | None = None


class AdapterError(Exception):
    """The renderer could not be run or produced unusable output."""


class RendererAdapter(Protocol):
    name: str

    def version(self, ws: Workspace) -> str | None:
        """Version string of the renderer that `render` would run, for cache keys."""
        ...

    def runtime_sha256(self) -> str:
        """Hash of the renderer-side runtime code shipped with Kinetic, for cache keys."""
        ...

    def render(self, job: ShotJob, run_dir: Path, ws: Workspace) -> ShotResult:
        """Render every frame of `job` into run_dir/frames. Raise AdapterError on failure.
        A zero exit code is not success: the adapter verifies frame count and size."""
        ...
