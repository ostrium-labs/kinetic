"""Render-plan compilation, cache keys and trace dedupe."""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ostrium_kinetic import canonical
from ostrium_kinetic.contract.loader import load_film, read_yaml
from ostrium_kinetic.plan.compiler import PlanError, RenderPlan, compile_plan, write_plan
from ostrium_kinetic.plan.simulate import run_film_sims
from ostrium_kinetic.workspace import Workspace

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "smoke-failover"

Edit = Callable[[dict[str, Any]], None]


def two_shots(doc: dict[str, Any]) -> None:
    """Add S2: the same simulation as S1, landscape only."""
    s2 = copy.deepcopy(doc["sequences"][0]["shots"][0])
    s2["id"] = "S2"
    del s2["framings"]["9:16"]
    doc["sequences"][0]["shots"].append(s2)


def make_film(tmp_path: Path, *edits: Edit) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "kinetic.toml").write_text('[cache]\ndir = ".kcache"\n', "utf-8")
    film_dir = tmp_path / "film"
    shutil.copytree(EXAMPLE, film_dir, ignore=shutil.ignore_patterns("__pycache__"))
    doc = read_yaml(film_dir / "film.yaml")
    for edit in edits:
        edit(doc)
    (film_dir / "film.yaml").write_text(json.dumps(doc, indent=1), "utf-8")
    return film_dir


def plan(film_dir: Path, profile: str = "preview") -> RenderPlan:
    return compile_plan(load_film(film_dir), Workspace.find(film_dir), profile)


def keys(p: RenderPlan) -> dict[str, str]:
    return {s.shot_id: s.cache_key for s in p.shots}


def test_preview_plan_of_example(tmp_path: Path) -> None:
    p = plan(make_film(tmp_path))
    assert (p.film_id, p.profile, p.width, p.height, p.total_frames) == (
        "kinetic.smoke-failover",
        "preview",
        1280,
        720,
        105,
    )
    (s1,) = p.shots
    assert (s1.shot_id, s1.sequence_id, s1.framing, s1.camera) == ("S1", "Q1", "16:9", "wide")
    assert (s1.frame_start, s1.frame_count, s1.tick_start, s1.tick_end) == (0, 105, 0, 3500)
    assert s1.trace_dir is not None and s1.trace_dir.startswith(".kcache/traces/")
    assert s1.trace_sha256 is not None and len(s1.trace_sha256) == 64
    assert s1.scene_sha256 is None


def test_plan_written_as_canonical_json(tmp_path: Path) -> None:
    film_dir = make_film(tmp_path)
    p = plan(film_dir)
    path = write_plan(p, Workspace.find(film_dir))
    assert path == tmp_path / ".kcache" / "plans" / p.film_id / "preview" / "render-plan.json"
    text = path.read_bytes().decode("utf-8")
    assert text == canonical.dumps(p.model_dump(mode="json")) + "\n"
    assert RenderPlan.model_validate_json(text) == p


def test_cache_keys_stable(tmp_path: Path) -> None:
    film_dir = make_film(tmp_path, two_shots)
    first, second = plan(film_dir), plan(film_dir)
    assert first == second
    assert len(set(keys(first).values())) == 2


def test_changing_one_shot_changes_only_its_key(tmp_path: Path) -> None:
    def tweak(doc: dict[str, Any]) -> None:
        doc["sequences"][0]["shots"][1]["sim"]["params"]["jitter_ms"] = 10

    before = keys(plan(make_film(tmp_path / "a", two_shots)))
    after = keys(plan(make_film(tmp_path / "b", two_shots, tweak)))
    assert before["S1"] == after["S1"]
    assert before["S2"] != after["S2"]


def test_reordering_keeps_keys(tmp_path: Path) -> None:
    def reorder(doc: dict[str, Any]) -> None:
        doc["sequences"][0]["shots"].reverse()

    a = plan(make_film(tmp_path / "a", two_shots))
    b = plan(make_film(tmp_path / "b", two_shots, reorder))
    assert keys(a) == keys(b)
    assert [s.shot_id for s in b.shots] == ["S2", "S1"]
    assert [s.frame_start for s in b.shots] == [0, 105]
    assert b.total_frames == 210


def test_scene_file_changes_key(tmp_path: Path) -> None:
    film_dir = make_film(tmp_path)
    without = plan(film_dir).shots[0]
    scene = film_dir / "scenes" / "godot" / "failover.tscn"
    scene.parent.mkdir(parents=True)
    scene.write_text("[gd_scene format=3]\n", "utf-8")
    with_scene = plan(film_dir).shots[0]
    assert with_scene.scene_sha256 == canonical.sha256_file(scene)
    assert with_scene.cache_key != without.cache_key


def test_social_plan_includes_only_vertical_shots(tmp_path: Path) -> None:
    p = plan(make_film(tmp_path, two_shots), "social")
    assert [s.shot_id for s in p.shots] == ["S1"]
    assert (p.width, p.height, p.total_frames) == (1080, 1920, 105)
    assert (p.shots[0].framing, p.shots[0].camera) == ("9:16", "tall")


def test_social_plan_needs_a_vertical_shot(tmp_path: Path) -> None:
    def landscape_only(doc: dict[str, Any]) -> None:
        del doc["sequences"][0]["shots"][0]["framings"]["9:16"]

    with pytest.raises(PlanError, match="no shot has a '9:16' framing"):
        plan(make_film(tmp_path, landscape_only), "social")


def test_profile_changes_keys(tmp_path: Path) -> None:
    film_dir = make_film(tmp_path)
    assert keys(plan(film_dir, "preview")) != keys(plan(film_dir, "production"))


def test_unknown_profile(tmp_path: Path) -> None:
    with pytest.raises(PlanError, match="unknown profile 'cinema'"):
        plan(make_film(tmp_path), "cinema")


def test_validation_errors_block_plan(tmp_path: Path) -> None:
    def broken(doc: dict[str, Any]) -> None:
        doc["sequences"][0]["shots"][0]["checkpoints"][1]["expect"][0]["equals"] = "up"

    with pytest.raises(PlanError, match=r"1 validation error\(s\)\n  shot S1 @frame 40"):
        plan(make_film(tmp_path, broken))


def test_run_film_sims_dedupes(tmp_path: Path) -> None:
    film_dir = make_film(tmp_path, two_shots)
    loaded, ws = load_film(film_dir), Workspace.find(film_dir)
    results = run_film_sims(loaded, ws)
    assert [shot for shot, _ in results] == ["S1", "S2"]
    assert results[0][1] is results[1][1]
    assert results[0][1].cached is False
    again = run_film_sims(loaded, ws, shot_id="S2")
    assert [(s, r.cached) for s, r in again] == [("S2", True)]
    forced = run_film_sims(loaded, ws, shot_id="S1", force=True)
    assert forced[0][1].cached is False
    assert forced[0][1].trace_sha256 == results[0][1].trace_sha256


def test_run_film_sims_errors(tmp_path: Path) -> None:
    def no_param(doc: dict[str, Any]) -> None:
        del doc["sequences"][0]["shots"][0]["sim"]["params"]["end_s"]

    film_dir = make_film(tmp_path, no_param)
    loaded, ws = load_film(film_dir), Workspace.find(film_dir)
    with pytest.raises(KeyError, match="S9"):
        run_film_sims(loaded, ws, shot_id="S9")
    with pytest.raises(ValueError, match="shot S1: simulation failed: KeyError"):
        run_film_sims(loaded, ws)
