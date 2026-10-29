"""Film validation: cross-references, evidence rules, files, timemaps and checkpoints."""

from __future__ import annotations

import copy
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from ostrium_kinetic.contract.loader import load_film, read_yaml
from ostrium_kinetic.contract.validate import Diagnostic, validate_film
from ostrium_kinetic.workspace import Workspace

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "smoke-failover"

Edit = Callable[[dict[str, Any]], None]


def make_film(tmp_path: Path, edit: Edit | None = None) -> Path:
    """Copy the example film into a tmp workspace (with its own cache) and apply `edit`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "kinetic.toml").write_text('[cache]\ndir = ".kcache"\n', "utf-8")
    film_dir = tmp_path / "film"
    shutil.copytree(EXAMPLE, film_dir, ignore=shutil.ignore_patterns("__pycache__"))
    if edit is not None:
        doc = read_yaml(film_dir / "film.yaml")
        edit(doc)
        (film_dir / "film.yaml").write_text(json.dumps(doc, indent=1), "utf-8")
    return film_dir


def run(tmp_path: Path, edit: Edit | None = None, run_sims: bool = True) -> list[Diagnostic]:
    film_dir = make_film(tmp_path, edit)
    ws = Workspace.find(film_dir)
    assert ws.cache_dir == tmp_path / ".kcache"
    return validate_film(load_film(film_dir), ws, run_sims=run_sims)


def errors(diags: list[Diagnostic]) -> list[str]:
    return [f"{d.where}: {d.message}" for d in diags if d.level == "error"]


def warnings(diags: list[Diagnostic]) -> list[str]:
    return [f"{d.where}: {d.message}" for d in diags if d.level == "warning"]


def shot(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["sequences"][0]["shots"][0]


def test_example_is_valid(tmp_path: Path) -> None:
    diags = run(tmp_path)
    assert errors(diags) == []
    assert warnings(diags) == ["shot S1: scene file not found: scenes/godot/failover.tscn"]
    assert (tmp_path / ".kcache" / "traces").is_dir()


def test_scene_present_no_warning(tmp_path: Path) -> None:
    film_dir = make_film(tmp_path)
    scene = film_dir / "scenes" / "godot" / "failover.tscn"
    scene.parent.mkdir(parents=True)
    scene.write_text("[gd_scene format=3]\n", "utf-8")
    diags = validate_film(load_film(film_dir), Workspace.find(film_dir), run_sims=False)
    assert diags == []


def test_duplicate_sequence_and_shot_ids(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["id"] = "Q1"

    assert "sequence/shot Q1: id 'Q1' is used 2 times" in errors(run(tmp_path, edit, False))


def test_duplicate_claim_source_narration_assumption_ids(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        doc["claims"].append(dict(doc["claims"][0]))
        doc["sources"] = [{"id": "s", "path": "a"}, {"id": "s", "path": "b"}]
        doc["narration"] = [{"id": "N", "text": "a"}, {"id": "N", "text": "b"}]
        doc["assumptions"].append(dict(doc["assumptions"][0]))

    errs = errors(run(tmp_path, edit, False))
    assert "claim C1: id 'C1' is used 2 times" in errs
    assert "source s: id 's' is used 2 times" in errs
    assert "narration N: id 'N' is used 2 times" in errs
    assert "assumption A1: id 'A1' is used 2 times" in errs


def test_unknown_references(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["claims"] = ["C1", "C9"]
        shot(doc)["narration"] = "N1"
        doc["claims"].append({"id": "C2", "text": "x", "evidence": "sourced", "source": "nowhere"})

    errs = errors(run(tmp_path, edit, False))
    assert "shot S1: unknown claim 'C9'" in errs
    assert "shot S1: unknown narration segment 'N1'" in errs
    assert "claim C2: unknown source 'nowhere'" in errs


def test_simulated_shot_needs_sim(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        del shot(doc)["sim"]
        del shot(doc)["checkpoints"]

    diags = run(tmp_path, edit)
    assert "shot S1: a simulated shot needs `sim`" in errors(diags)
    assert "shot S1: `timemap` has no effect without `sim`" in warnings(diags)


def test_trace_evidence_not_supported_yet(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["evidence"] = "trace"

    errs = errors(run(tmp_path, edit))
    assert len(errs) == 1
    assert "recorded traces arrive in a later milestone" in errs[0]


def test_illustrative_shot_rules(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["evidence"] = "illustrative"
        doc["sources"] = [{"id": "post", "url": "https://example.org/post"}]
        doc["claims"].append({"id": "C2", "text": "x", "evidence": "sourced", "source": "post"})
        shot(doc)["claims"] = ["C1", "C2"]

    diags = run(tmp_path, edit, False)
    assert errors(diags) == ["shot S1: an illustrative shot must not have `sim`"]
    assert any("references sourced claim 'C2'" in w for w in warnings(diags))
    assert not any("'C1'" in w for w in warnings(diags))


def test_sim_needs_timemap(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        del shot(doc)["timemap"]

    assert "shot S1: a shot with `sim` needs a `timemap`" in errors(run(tmp_path, edit))


def test_timemap_must_move_forward(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["timemap"] = {"sim_start_s": "2", "sim_end_s": "1"}

    errs = errors(run(tmp_path, edit, False))
    assert "shot S1: timemap: sim_end_s must be greater than sim_start_s" in errs


def test_time_compression_needs_label(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["frames"] = 210  # 7 s of film for 3.5 s of sim: half speed
        shot(doc)["checkpoints"] = []

    errs = errors(run(tmp_path, edit))
    assert len(errs) == 1 and "0.5x real time (1/2)" in errs[0] and "`label`" in errs[0]

    def labelled(doc: dict[str, Any]) -> None:
        edit(doc)
        shot(doc)["timemap"]["label"] = "time slowed 2x"

    assert errors(run(tmp_path / "b", labelled)) == []


def test_missing_sim_module(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["sim"]["module"] = "sim/nope.py"

    assert errors(run(tmp_path, edit)) == ["shot S1: simulation module not found: sim/nope.py"]


def test_checkpoint_frame_outside_shot(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["checkpoints"][0]["at_frame"] = 105

    assert errors(run(tmp_path, edit)) == [
        "shot S1 @frame 105: checkpoint frame is outside the shot (105 frames)"
    ]


def test_timemap_outside_trace(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["timemap"] = {"sim_start_s": "0.5", "sim_end_s": "4", "label": "sped up"}
        shot(doc)["checkpoints"] = []

    errs = errors(run(tmp_path, edit))
    assert len(errs) == 1 and "not within the trace [0, 3500]" in errs[0]


def test_timemap_whole_ticks(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["timemap"] = {"sim_start_s": "0", "sim_end_s": "3.4995", "label": "sped up"}
        shot(doc)["checkpoints"] = []

    errs = errors(run(tmp_path, edit))
    assert len(errs) == 1 and "not a whole number of ticks" in errs[0]


def test_simulation_failure_is_reported(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        del shot(doc)["sim"]["params"]["timeout_s"]

    errs = errors(run(tmp_path, edit))
    assert errs == ["shot S1: simulation failed: KeyError: 'timeout_s'"]
    assert errors(run(tmp_path / "b", edit, run_sims=False)) == []


def test_checkpoint_failure_is_precise(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["checkpoints"][1]["expect"][0]["equals"] = "up"

    assert errors(run(tmp_path, edit)) == [
        "shot S1 @frame 40: checkpoint failed at tick 1333: link:P status "
        "expected 'up', got 'failed'"
    ]


@pytest.mark.parametrize(
    ("frame", "entity", "field", "equals", "actual"),
    [
        (30, "packet:1", "active_span.via", "link:S", "'link:P'"),
        (30, "packet:1", "active_span.kind", "hop", "'transit'"),
        (60, "packet:1", "active_span", None, None),  # passes: no active span
        (60, "packet:1", "active_span.via", "link:P", "<absent>"),
        (30, "packet:2", "status", "in_flight", "<absent>"),  # not created yet
        (40, "link:P", "colour", "red", "<absent>"),
        (30, "packet:1", "active_span", None, "'tx:packet:1'"),
    ],
)
def test_expectation_forms(
    tmp_path: Path, frame: int, entity: str, field: str, equals: Any, actual: str | None
) -> None:
    def edit(doc: dict[str, Any]) -> None:
        cp = {"at_frame": frame, "expect": [{"entity": entity, "field": field, "equals": equals}]}
        shot(doc)["checkpoints"] = [copy.deepcopy(cp)]

    errs = errors(run(tmp_path, edit))
    if actual is None:
        assert errs == []
    else:
        assert len(errs) == 1
        assert errs[0].endswith(f"{entity} {field} expected {equals!r}, got {actual}")


def test_bool_does_not_equal_int(tmp_path: Path) -> None:
    def edit(doc: dict[str, Any]) -> None:
        shot(doc)["checkpoints"] = [
            {"at_frame": 0, "expect": [{"entity": "link:P", "field": "status", "equals": True}]}
        ]

    assert len(errors(run(tmp_path, edit))) == 1
