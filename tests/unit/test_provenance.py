from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ostrium_kinetic import CONTRACT_VERSION, __version__
from ostrium_kinetic.provenance import build_provenance, git_state, write_provenance


def _build(ws: Path, outputs: list[Path], **over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "film_id": "kinetic.smoke-failover",
        "film_sha256": "f" * 64,
        "profile": "preview",
        "plan_sha256": "p" * 64,
        "shots": [
            {
                "shot_id": "S1",
                "render_key": "k" * 64,
                "trace_sha256": "t" * 64,
                "scene_sha256": "s" * 64,
                "renderer": "godot",
                "renderer_version": "4.7.2",
                "seconds": 12.5,  # wall time: not provenance-deterministic, dropped
            }
        ],
        "tools": {"godot": "4.7.2", "ffmpeg": "9.0.2", "blender": None},
        "outputs": outputs,
        "workspace_root": ws,
        "kinetic_root": ws,
        "git": lambda _p: ("abc123", False),
    }
    args.update(over)
    return build_provenance(**args)


def test_provenance_shape_and_hashes(tmp_path: Path) -> None:
    out = tmp_path / "out" / "preview" / "preview.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"\x00\x01\r\n" * 100)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.bin"
    outside.write_bytes(b"x")
    try:
        data = _build(tmp_path, [out, outside])
    finally:
        outside.unlink()
    assert set(data) == {
        "deterministic",
        "deterministic_sha256",
        "outputs",
        "environment",
        "created_utc",
    }
    det = data["deterministic"]
    assert det["film"] == {"id": "kinetic.smoke-failover", "sha256": "f" * 64}
    assert det["kinetic_version"] == __version__ and det["contract"] == CONTRACT_VERSION
    assert list(det["tools"]) == ["blender", "ffmpeg", "godot"]
    assert det["shots"][0] == {
        "shot_id": "S1",
        "render_key": "k" * 64,
        "trace_sha256": "t" * 64,
        "scene_sha256": "s" * 64,
        "renderer": "godot",
        "renderer_version": "4.7.2",
    }
    first = data["outputs"][0]
    assert first["path"] == "out/preview/preview.mp4"
    assert first["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()  # raw bytes, CRLF kept
    assert first["bytes"] == 400
    assert Path(data["outputs"][1]["path"]).is_absolute()
    git = data["environment"]["git"]
    assert git == {
        "kinetic": "abc123",
        "kinetic_dirty": False,
        "workspace": "abc123",
        "workspace_dirty": False,
    }
    assert data["created_utc"].endswith("+00:00")

    path = write_provenance(tmp_path / "out" / "provenance.json", data)
    raw = path.read_bytes()
    assert b"\r\n" not in raw and json.loads(raw) == data
    assert [p.name for p in path.parent.iterdir()] == ["preview", "provenance.json"]


def test_deterministic_part_is_stable(tmp_path: Path) -> None:
    a = _build(tmp_path, [], git=lambda _p: ("one", True))
    b = _build(tmp_path, [], git=lambda _p: ("two", False))
    assert a["deterministic"] == b["deterministic"]
    assert a["deterministic_sha256"] == b["deterministic_sha256"]
    c = _build(tmp_path, [], tools={"godot": "4.7.3", "ffmpeg": "9.0.2", "blender": None})
    assert c["deterministic_sha256"] != a["deterministic_sha256"]


def test_git_state_outside_a_repo(tmp_path: Path) -> None:
    # tmp dirs live under the system temp directory, which is not a git checkout.
    assert git_state(tmp_path / "missing") == (None, None)
