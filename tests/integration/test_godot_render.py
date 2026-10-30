"""Render the smoke-failover shot with Godot and check frames and annotations against the trace.

Pixel alignment: the packet:1 pulse is the only near-white-yellow object on screen, so its pixel
centroid in each frame must sit at the centre of the bbox annotated for that same frame (and
not the neighbouring frames', which are several pixels away). This proves annotations are
collected after the scene has updated and before the frame is drawn.
"""

from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from ostrium_kinetic.adapters.godot import ERROR_FILE, GodotAdapter
from ostrium_kinetic.env.tools import discover, resolve
from ostrium_kinetic.job import AdapterError, ShotJob, frame_name
from ostrium_kinetic.sim import Trace
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.workspace import Workspace

_FOUND = resolve(discover("godot", None, probe=False))
GODOT = Path(_FOUND.path) if _FOUND else None

pytestmark = [
    pytest.mark.renderer("godot"),
    pytest.mark.skipif(GODOT is None, reason="Godot not found (kinetic doctor)"),
]

TRACKED = ["node:A", "node:B", "link:P", "link:S", "packet:1", "packet:2", "packet:ack2"]


def read_png_rgb(path: Path) -> np.ndarray:
    """Decode an 8-bit, non-interlaced RGB/RGBA PNG to an (h, w, 3) int array."""
    data = path.read_bytes()
    pos, idat = 8, []
    width = height = bpp = 0
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        kind, body = data[pos + 4 : pos + 8], data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _, _, interlace = struct.unpack(">IIBBBBB", body)
            assert depth == 8 and interlace == 0 and ctype in (2, 6)
            bpp = 3 if ctype == 2 else 4
        elif kind == b"IDAT":
            idat.append(body)
    stride = width * bpp
    raw = np.frombuffer(zlib.decompress(b"".join(idat)), dtype=np.uint8).reshape(height, -1)
    out = np.zeros((height, stride), dtype=np.int64)
    prev = np.zeros(stride, dtype=np.int64)
    for y in range(height):
        kind, line = raw[y, 0], raw[y, 1:].astype(np.int64)
        if kind == 0:
            cur = line
        elif kind == 2:
            cur = (line + prev) & 255
        else:
            cur = np.zeros(stride, dtype=np.int64)
            for x in range(stride):
                a = int(cur[x - bpp]) if x >= bpp else 0
                b = int(prev[x])
                c = int(prev[x - bpp]) if x >= bpp else 0
                if kind == 1:
                    p = a
                elif kind == 3:
                    p = (a + b) // 2
                else:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    p = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                cur[x] = (int(line[x]) + p) & 255
        out[y], prev = cur, cur
    return out.reshape(height, width, bpp)[:, :, :3]


@pytest.fixture(scope="module")
def setup(
    tmp_path_factory: pytest.TempPathFactory,
    repo_root: Path,
    failover_params: dict[str, Any],
) -> tuple[Workspace, Path, Trace, Path]:
    root = tmp_path_factory.mktemp("godot-render")
    (root / "kinetic.toml").write_text('[cache]\ndir = "cache"\n', "utf-8")
    ws = Workspace.find(root)
    film = repo_root / "examples" / "smoke-failover"
    ref = ensure_trace(film / "sim" / "failover.py", failover_params, 7, ws.cache_dir)
    return ws, film, Trace.load(ref.directory), ref.directory


def make_job(film: Path, trace_dir: Path, run: Path, **over: Any) -> ShotJob:
    fields: dict[str, Any] = {
        "film_id": "kinetic.smoke-failover",
        "shot_id": "S1",
        "renderer": "godot",
        "film_dir": film.as_posix(),
        "scene": "scenes/godot/failover.tscn",
        "fps": 30,
        "width": 320,
        "height": 180,
        "frame_count": 105,
        "framing": "16:9",
        "camera": "wide",
        "quality": "draft",
        "trace_dir": trace_dir.as_posix(),
        "tick_rate": "1/1000",
        "tick_start": 0,
        "tick_end": 3500,
        "out_dir": run.as_posix(),
        "evidence": "simulated",
    }
    fields.update(over)
    return ShotJob.model_validate(fields)


@pytest.fixture(scope="module")
def rendered(
    setup: tuple[Workspace, Path, Trace, Path],
) -> tuple[ShotJob, Path, list[dict[str, Any]]]:
    ws, film, _, trace_dir = setup
    run = ws.cache("runs", "s1")
    job = make_job(film, trace_dir, run)
    result = GodotAdapter().render(job, run, ws)
    assert result.status == "frames_ok"
    assert result.renderer_version and result.renderer_version.startswith("4.7")
    lines = (run / "annotations.jsonl").read_text("utf-8").splitlines()
    return job, run, [json.loads(line) for line in lines]


def test_frames_and_side_effects(rendered: tuple[ShotJob, Path, list[dict[str, Any]]]) -> None:
    job, run, annotations = rendered
    frames = sorted(p.name for p in (run / "frames").iterdir())
    assert frames == [frame_name(i) for i in range(105)]  # no stray frame.wav
    assert len(annotations) == 105
    assert not (run / ERROR_FILE).exists()
    assert (run / "job.json").is_file() and (run / "render.log").is_file()
    # The scene draws the job's evidence badge and registers it with Kinetic.label.
    text, box = annotations[0]["labels"][0]
    assert text == "SIMULATED"
    assert 0 <= box[0] < box[2] <= job.width and 0 <= box[1] < box[3] <= job.height


def test_annotations_match_trace(
    setup: tuple[Workspace, Path, Trace, Path],
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    _, _, trace, _ = setup
    job, _, annotations = rendered
    for f, ann in enumerate(annotations):
        tick = job.tick_at(f)
        assert tick is not None
        assert ann["frame"] == f and ann["tick"] == tick
        assert sorted(ann["entities"]) == sorted(TRACKED)
        state = trace.state_at(tick)
        for entity in TRACKED:
            got = ann["entities"][entity]
            assert got["state"] == state.get(entity, {}), (f, entity)
            active = trace.active_span_of(entity, tick)
            if active is None:
                assert got["active_span"] is None, (f, entity)
            else:
                span = got["active_span"]
                assert span["span_id"] == active.span.span_id
                assert span["via"] == active.span.attrs["via"]
                assert span["progress"] == pytest.approx(float(active.progress), abs=1e-9)
                assert span["planned_progress"] == pytest.approx(
                    float(active.planned_progress), abs=1e-9
                )
            if entity.startswith("packet:"):
                # A pulse is drawn while its packet is in transit, and stays docked at the
                # destination once delivered; a lost packet is not drawn.
                delivered = state.get(entity, {}).get("status") == "delivered"
                assert got["visible"] == (active is not None or delivered), (f, entity)
            else:
                assert got["visible"] and got["bbox"] is not None, (f, entity)


def test_checkpoints_and_pulse_position(
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    _, _, annotations = rendered
    at30 = annotations[30]["entities"]
    assert at30["packet:1"]["active_span"]["via"] == "link:P"
    assert at30["packet:1"]["visible"]

    def cx(box: list[float]) -> float:
        return (box[0] + box[2]) / 2

    a, b, pulse = (cx(at30[e]["bbox"]) for e in ("node:A", "node:B", "packet:1"))
    assert a < pulse < b
    assert annotations[40]["entities"]["link:P"]["state"]["status"] == "failed"
    assert annotations[60]["entities"]["packet:2"]["active_span"]["via"] == "link:S"
    assert annotations[90]["entities"]["packet:2"]["state"]["status"] == "delivered"


def test_annotations_align_with_pixels(
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    _, run, annotations = rendered

    def centre(f: int) -> tuple[float, float]:
        box = annotations[f]["entities"]["packet:1"]["bbox"]
        return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2

    for f in (3, 12, 21, 30):
        img = read_png_rgb(run / "frames" / frame_name(f))
        mask = (img[:, :, 0] >= 245) & (img[:, :, 1] >= 240) & (img[:, :, 2] < 230)
        ys, xs = np.nonzero(mask)
        assert len(xs) > 10, f"pulse not found in frame {f}"
        px, py = xs.mean() + 0.5, ys.mean() + 0.5  # pixel centres
        errors = {g: abs(centre(g)[0] - px) + abs(centre(g)[1] - py) for g in (f - 1, f, f + 1)}
        assert errors[f] < 1.5, (f, (px, py), centre(f))
        assert errors[f] < min(errors[f - 1], errors[f + 1]), (f, errors)


def test_rerender_is_byte_identical(
    setup: tuple[Workspace, Path, Trace, Path],
    rendered: tuple[ShotJob, Path, list[dict[str, Any]]],
) -> None:
    ws = setup[0]
    job, run, _ = rendered
    run2 = ws.cache("runs", "s1-again")
    GodotAdapter().render(job.model_copy(update={"out_dir": run2.as_posix()}), run2, ws)
    for i in range(job.frame_count):
        name = frame_name(i)
        assert (run / "frames" / name).read_bytes() == (run2 / "frames" / name).read_bytes(), name
    assert (run / "annotations.jsonl").read_bytes() == (run2 / "annotations.jsonl").read_bytes()


def test_missing_camera_fails_with_runtime_error(
    setup: tuple[Workspace, Path, Trace, Path],
) -> None:
    ws, film, _, trace_dir = setup
    run = ws.cache("runs", "bad-camera")
    job = make_job(film, trace_dir, run, camera="nope", frame_count=5)
    with pytest.raises(AdapterError, match="no Camera3D named 'nope'"):
        GodotAdapter().render(job, run, ws)
    assert (run / ERROR_FILE).is_file()
