"""The render -> assemble -> QA -> provenance pipeline, end to end through the CLI.

Each workspace is a tmp copy of examples/smoke-failover with its own kinetic.toml (cache inside
tmp_path, discovered tool paths written in), so the repo's .kinetic/ and out/ are never touched.
The preview profile keeps the one real Godot render to about ten seconds; every later command
in the module works from that render's cache.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ostrium_kinetic.cli.main import app
from ostrium_kinetic.env.tools import discover, resolve
from ostrium_kinetic.sim.runner import ensure_trace
from ostrium_kinetic.workspace import Workspace

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "smoke-failover"
FILM_ID = "kinetic.smoke-failover"
CACHE = "cache"

_TOOLS = {
    name: resolve(discover(name, None, probe=False)) for name in ("godot", "ffmpeg", "ffprobe")
}
_MISSING = sorted(name for name, found in _TOOLS.items() if found is None)

pytestmark = [
    pytest.mark.renderer("godot"),
    pytest.mark.renderer("ffmpeg"),
    pytest.mark.skipif(bool(_MISSING), reason=f"not found: {', '.join(_MISSING)} (kinetic doctor)"),
]

runner = CliRunner()


def make_workspace(root: Path) -> Path:
    """A workspace at `root` holding a copy of the smoke film; returns the film directory."""
    lines = ["[tools]"]
    for name, found in _TOOLS.items():
        assert found is not None
        lines.append(f"{name} = {json.dumps(Path(found.path).as_posix())}")
    lines += ["", "[cache]", f'dir = "{CACHE}"', ""]
    (root / "kinetic.toml").write_text("\n".join(lines), "utf-8")
    film = root / "film"
    shutil.copytree(EXAMPLE, film, ignore=shutil.ignore_patterns("__pycache__", ".godot"))
    return film


def run_dirs(root: Path) -> set[str]:
    shots = root / CACHE / "shots"
    return {p.name for p in shots.iterdir() if p.is_dir()} if shots.is_dir() else set()


def invoke(*args: str) -> Any:
    return runner.invoke(app, list(args))


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Any]:
    """One real `kinetic build --profile preview`; returns (root, film, result)."""
    root = tmp_path_factory.mktemp("pipeline")
    film = make_workspace(root)
    result = invoke("build", str(film), "--profile", "preview")
    return root, film, result


def test_build_writes_verified_outputs(
    built: tuple[Path, Path, Any], failover_params: dict[str, Any]
) -> None:
    root, _, result = built
    assert result.exit_code == 0, result.output
    assert "S1: rendering 105 frames with godot" in result.output
    out = root / "out" / FILM_ID / "preview"
    for name in ("master.mkv", "film.mp4", "contact/S1.png", "qa/report.json"):
        assert (out / name).is_file(), name
        assert (out / name).stat().st_size > 0, name

    report = json.loads((out / "qa" / "report.json").read_text("utf-8"))
    assert report["film_id"] == FILM_ID and report["profile"] == "preview"
    assert report["checks"]
    assert [c for c in report["checks"] if c["status"] == "fail"] == []
    ids = {c["id"] for c in report["checks"]}
    assert {"frames.count", "annotations.parity", "annotations.checkpoint"} <= ids

    (key,) = run_dirs(root)
    shot = json.loads((root / CACHE / "shots" / key / "shot.json").read_text("utf-8"))
    assert shot["status"] == "mezzanine_ok" and shot["render_key"].startswith(key)

    ws = Workspace.find(root)
    trace = ensure_trace(EXAMPLE / "sim" / "failover.py", failover_params, 7, ws.cache_dir)
    prov = json.loads((out / "provenance.json").read_text("utf-8"))
    (prov_shot,) = prov["deterministic"]["shots"]
    assert prov_shot["shot_id"] == "S1"
    assert prov_shot["render_key"] == shot["render_key"]
    assert prov_shot["trace_sha256"] == trace.trace_sha256
    assert prov["deterministic"]["film"]["id"] == FILM_ID
    assert prov["deterministic_sha256"]


def test_second_build_is_cached(built: tuple[Path, Path, Any]) -> None:
    root, film, first = built
    assert first.exit_code == 0, first.output
    before = run_dirs(root)
    result = invoke("build", str(film), "--profile", "preview")
    assert result.exit_code == 0, result.output
    assert "S1: cached" in result.output
    assert "rendering" not in result.output
    assert run_dirs(root) == before


def edit_film(film: Path, old: str, new: str) -> str:
    """Replace the one occurrence of `old` in film.yaml; returns the original text."""
    spec = film / "film.yaml"
    original = spec.read_text("utf-8")
    assert original.count(old) == 1
    spec.write_text(original.replace(old, new), "utf-8")
    return original


def qa_fails(root: Path) -> list[dict[str, Any]]:
    report = root / "out" / FILM_ID / "preview" / "qa" / "report.json"
    return [c for c in json.loads(report.read_text("utf-8"))["checks"] if c["status"] == "fail"]


def test_checkpoint_wrong_on_the_trace_stops_before_rendering(
    built: tuple[Path, Path, Any],
) -> None:
    root, film, first = built
    assert first.exit_code == 0, first.output
    before = run_dirs(root)
    good = '{ entity: "link:P", field: status, equals: failed }'
    original = edit_film(film, good, good.replace("failed", "up"))
    try:
        result = invoke("build", str(film), "--profile", "preview")
    finally:
        (film / "film.yaml").write_text(original, "utf-8")
    assert result.exit_code == 1, result.output
    assert "checkpoint failed" in result.output and "expected 'up'" in result.output
    assert "rendering" not in result.output
    assert run_dirs(root) == before


def test_checkpoint_wrong_on_the_annotations_fails_qa_without_rerender(
    built: tuple[Path, Path, Any],
) -> None:
    """packet:1 is lost at frame 90 on the trace (so `plan` accepts the checkpoint), but a lost
    packet is not drawn: the renderer's testimony says it is not visible, and QA fails."""
    root, film, first = built
    assert first.exit_code == 0, first.output
    before = run_dirs(root)
    good = '- { entity: "packet:2", field: status, equals: delivered }'
    extra = '- { entity: "packet:1", field: status, equals: lost }'
    original = edit_film(film, good, f"{good}\n              {extra}")
    try:
        broken = invoke("build", str(film), "--profile", "preview")
        assert broken.exit_code == 1, broken.output
        assert "S1: cached" in broken.output and "rendering" not in broken.output
        assert "annotations.checkpoint" in broken.output
        fails = qa_fails(root)
        assert fails and {c["id"] for c in fails} == {"annotations.checkpoint"}
        assert all(c["shot_id"] == "S1" for c in fails)
        # the semantic gate: a shot failing QA never becomes part of a deliverable
        delivery = root / "out" / FILM_ID / "preview" / "film.mp4"
        stamp = delivery.stat().st_mtime_ns
        refused = invoke("assemble", str(film), "--profile", "preview")
        assert refused.exit_code == 1, refused.output
        assert "shots failing QA" in refused.output
        assert delivery.stat().st_mtime_ns == stamp
    finally:
        (film / "film.yaml").write_text(original, "utf-8")
    fixed = invoke("build", str(film), "--profile", "preview")
    assert fixed.exit_code == 0, fixed.output
    assert "S1: cached" in fixed.output and "rendering" not in fixed.output
    assert qa_fails(root) == []
    assert run_dirs(root) == before


def test_assemble_after_render_of_the_only_shot(built: tuple[Path, Path, Any]) -> None:
    root, film, first = built
    assert first.exit_code == 0, first.output
    out = root / "out" / FILM_ID / "preview"
    (out / "film.mp4").unlink()
    rendered = invoke("render", str(film), "--profile", "preview", "--shot", "S1")
    assert rendered.exit_code == 0, rendered.output
    assembled = invoke("assemble", str(film), "--profile", "preview")
    assert assembled.exit_code == 0, assembled.output
    assert (out / "film.mp4").is_file() and (out / "provenance.json").is_file()


def test_render_unknown_shot_is_a_clear_error(built: tuple[Path, Path, Any]) -> None:
    _, film, _ = built
    result = invoke("render", str(film), "--profile", "preview", "--shot", "NOPE")
    assert result.exit_code in (1, 2), result.output
    assert "'NOPE'" in result.output and "not in" in result.output


def test_qa_with_nothing_rendered_fails(tmp_path: Path) -> None:
    film = make_workspace(tmp_path)
    result = invoke("qa", str(film), "--profile", "social")
    assert result.exit_code == 1, result.output
    report = tmp_path / "out" / FILM_ID / "social" / "qa" / "report.json"
    checks = json.loads(report.read_text("utf-8"))["checks"]
    assert any(c["id"] == "shot.status" and c["status"] == "fail" for c in checks)
    assert not (tmp_path / "out" / FILM_ID / "social" / "social-9x16.mp4").exists()


def test_standalone_qa_keeps_and_reruns_delivery_checks(built: tuple[Path, Path, Any]) -> None:
    """`kinetic qa` after a build must not overwrite the full report with a shots-only one."""
    root, film, first = built
    assert first.exit_code == 0, first.output
    result = invoke("qa", str(film), "--profile", "preview")
    assert result.exit_code == 0, result.output
    report = root / "out" / FILM_ID / "preview" / "qa" / "report.json"
    checks = {c["id"]: c for c in json.loads(report.read_text("utf-8"))["checks"]}
    assert checks["delivery.current"]["status"] == "pass"
    for cid in ("delivery.color", "delivery.frames", "media.black", "media.freeze"):
        assert checks[cid]["status"] == "pass", cid


def test_standalone_qa_without_a_delivery_says_so(tmp_path: Path) -> None:
    film = make_workspace(tmp_path)
    result = invoke("qa", str(film), "--profile", "preview")
    assert result.exit_code == 1, result.output
    report = tmp_path / "out" / FILM_ID / "preview" / "qa" / "report.json"
    checks = {c["id"]: c for c in json.loads(report.read_text("utf-8"))["checks"]}
    assert checks["delivery.current"]["status"] == "fail"
    assert "kinetic assemble" in checks["delivery.current"]["detail"]
