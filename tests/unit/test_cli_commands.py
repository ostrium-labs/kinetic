"""The validate, sim, plan and schema commands, end to end in a tmp workspace."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ostrium_kinetic.cli.main import app

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "smoke-failover"

runner = CliRunner()


@pytest.fixture
def film_dir(tmp_path: Path) -> Path:
    (tmp_path / "kinetic.toml").write_text('[cache]\ndir = ".kcache"\n', "utf-8")
    target = tmp_path / "film"
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("__pycache__"))
    return target


def test_validate_ok(film_dir: Path) -> None:
    result = runner.invoke(app, ["validate", str(film_dir)])
    assert result.exit_code == 0, result.output
    assert "warning: shot S1: scene file not found" in result.output
    assert "ok: kinetic.smoke-failover (1 warning(s))" in result.output
    assert (film_dir.parent / ".kcache" / "traces").is_dir()


def test_validate_no_sim(film_dir: Path) -> None:
    result = runner.invoke(app, ["validate", str(film_dir), "--no-sim"])
    assert result.exit_code == 0, result.output
    assert not (film_dir.parent / ".kcache" / "traces").exists()


def test_validate_reports_checkpoint_failure(film_dir: Path) -> None:
    path = film_dir / "film.yaml"
    path.write_text(path.read_text("utf-8").replace("equals: failed", "equals: up"), "utf-8")
    result = runner.invoke(app, ["validate", str(film_dir)])
    assert result.exit_code == 1
    assert "error: shot S1 @frame 40: checkpoint failed at tick 1333" in result.output


def test_validate_load_error(film_dir: Path) -> None:
    path = film_dir / "film.yaml"
    path.write_text(path.read_text("utf-8").replace("frames: 105", "frames: -1"), "utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 1
    assert "sequences[0].shots[0].frames" in result.output


def test_sim_runs_then_caches(film_dir: Path) -> None:
    first = runner.invoke(app, ["sim", str(film_dir)])
    assert first.exit_code == 0, first.output
    assert first.output.startswith("S1: ran trace ")
    second = runner.invoke(app, ["sim", str(film_dir), "--shot", "S1"])
    assert second.exit_code == 0, second.output
    assert second.output.startswith("S1: cached trace ")
    forced = runner.invoke(app, ["sim", str(film_dir), "--force"])
    assert forced.output.startswith("S1: ran trace ")


def test_sim_unknown_shot(film_dir: Path) -> None:
    result = runner.invoke(app, ["sim", str(film_dir), "--shot", "S9"])
    assert result.exit_code == 2  # usage error
    assert "S9" in result.output


@pytest.mark.parametrize(("profile", "size"), [("preview", "1280x720"), ("social", "1080x1920")])
def test_plan(film_dir: Path, profile: str, size: str) -> None:
    result = runner.invoke(app, ["plan", str(film_dir), "--profile", profile])
    assert result.exit_code == 0, result.output
    assert f"S1: godot 105f @ {size} key " in result.output
    path = film_dir.parent / ".kcache" / "plans" / "kinetic.smoke-failover" / profile
    plan = json.loads((path / "render-plan.json").read_text("utf-8"))
    assert plan["profile"] == profile and plan["total_frames"] == 105


def test_plan_unknown_profile(film_dir: Path) -> None:
    result = runner.invoke(app, ["plan", str(film_dir), "--profile", "cinema"])
    assert result.exit_code == 2  # usage error
    assert "unknown profile 'cinema'" in result.output


def test_schema_check() -> None:
    result = runner.invoke(app, ["schema", "--check"])
    assert result.exit_code == 0, result.output
    assert "schemas up to date" in result.output
