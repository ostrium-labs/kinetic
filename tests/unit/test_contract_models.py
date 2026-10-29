"""Film contract models and the YAML loader."""

from __future__ import annotations

import shutil
from fractions import Fraction
from pathlib import Path

import pytest
from pydantic import ValidationError

from ostrium_kinetic.contract.loader import FilmLoadError, load_film
from ostrium_kinetic.contract.models import (
    PROFILES,
    Claim,
    Evidence,
    Expectation,
    Film,
    Shot,
    Source,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "smoke-failover"

MINIMAL = """\
kinetic: "0.1"
id: t.minimal
title: Minimal
fps: {fps}
sequences:
  - id: Q1
    title: One
    shots:
      - id: S1
        intent: Something happens.
        renderer: manim
        scene: scenes/x.py
        evidence: hypothetical
        frames: {frames}
        timemap: {{ sim_start_s: 0, sim_end_s: {end} }}
        framings:
          "16:9": {{ camera: wide }}
{extra}"""


def write_film(
    tmp_path: Path, fps: str = "30", frames: str = "36", end: str = "1.2", extra: str = ""
) -> Path:
    path = tmp_path / "film.yaml"
    path.write_text(MINIMAL.format(fps=fps, frames=frames, end=end, extra=extra), "utf-8")
    return path


def test_example_film_loads() -> None:
    loaded = load_film(EXAMPLE)
    assert loaded.film.id == "kinetic.smoke-failover"
    assert loaded.film_path == (EXAMPLE / "film.yaml").resolve()
    assert loaded.film_dir == EXAMPLE.resolve()
    shot = loaded.film.sequences[0].shots[0]
    assert shot.sim is not None and shot.sim.params["latency_p_s"] == "1.5"
    assert shot.timemap is not None and shot.timemap.sim_end_s == Fraction(7, 2)
    assert [c.id for c in loaded.claims] == ["C1"]


def test_yaml_decimals_are_exact(tmp_path: Path) -> None:
    loaded = load_film(write_film(tmp_path, fps="29.97", end="1.2"))
    assert loaded.film.fps == Fraction(2997, 100)
    tm = loaded.film.sequences[0].shots[0].timemap
    assert tm is not None
    assert tm.sim_end_s == Fraction(6, 5)
    assert tm.sim_end_s != Fraction(1.2)  # not the binary float value


def test_quoted_and_rational_strings_are_exact(tmp_path: Path) -> None:
    loaded = load_film(write_film(tmp_path, fps='"30000/1001"', end='"0.1"'))
    assert loaded.film.fps == Fraction(30000, 1001)
    tm = loaded.film.sequences[0].shots[0].timemap
    assert tm is not None and tm.sim_end_s == Fraction(1, 10)


def test_exact_serialises_as_string() -> None:
    film = load_film(EXAMPLE).film
    dumped = film.model_dump(mode="json")
    assert dumped["fps"] == "30"
    assert dumped["sequences"][0]["shots"][0]["timemap"]["sim_end_s"] == "7/2"
    assert Film.model_validate(dumped) == film


def test_load_accepts_file_path(tmp_path: Path) -> None:
    path = write_film(tmp_path)
    assert load_film(path).film.id == "t.minimal"
    assert load_film(tmp_path).film_path == path.resolve()


def test_error_paths_are_readable(tmp_path: Path) -> None:
    path = write_film(tmp_path, frames="0")
    with pytest.raises(FilmLoadError) as info:
        load_film(path)
    text = str(info.value)
    assert "sequences[0].shots[0].frames: Input should be greater than 0" in text
    assert str(path.resolve()) in text


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(FilmLoadError, match=r"sequences\[0\]\.shots\[0\]\.colour"):
        load_film(write_film(tmp_path, extra="        colour: red\n"))


def test_sourced_claim_needs_source(tmp_path: Path) -> None:
    extra = "claims:\n  - { id: C1, text: Eleven nines., evidence: sourced }\n"
    with pytest.raises(FilmLoadError) as info:
        load_film(write_film(tmp_path, extra=extra))
    assert "claims[0]: a sourced claim needs a source" in str(info.value)
    assert "inline" not in str(info.value)


def test_claims_file_is_resolved(tmp_path: Path) -> None:
    (tmp_path / "claims.yaml").write_text(
        "- { id: C1, text: Measured once., evidence: measured, source: lab }\n", "utf-8"
    )
    loaded = load_film(write_film(tmp_path, extra="claims: claims.yaml\n"))
    assert loaded.film.claims == "claims.yaml"
    assert [(c.id, c.evidence) for c in loaded.claims] == [("C1", Evidence.MEASURED)]


def test_claims_file_errors(tmp_path: Path) -> None:
    with pytest.raises(FilmLoadError, match=r"claims: file not found: missing\.yaml"):
        load_film(write_film(tmp_path, extra="claims: missing.yaml\n"))
    (tmp_path / "bad.yaml").write_text("- { id: C1, text: x, evidence: rumour }\n", "utf-8")
    with pytest.raises(FilmLoadError, match=r"bad\.yaml.*\n  \[0\]\.evidence"):
        load_film(write_film(tmp_path, extra="claims: bad.yaml\n"))


def test_invalid_yaml_and_missing_file(tmp_path: Path) -> None:
    bad = tmp_path / "film.yaml"
    bad.write_text("kinetic: [unclosed\n", "utf-8")
    with pytest.raises(FilmLoadError, match="invalid YAML"):
        load_film(bad)
    with pytest.raises(FilmLoadError, match="no such film file"):
        load_film(tmp_path / "nope")
    bad.write_text("- just a list\n", "utf-8")
    with pytest.raises(FilmLoadError, match="mapping"):
        load_film(bad)


def test_copied_example_round_trips(tmp_path: Path) -> None:
    shutil.copytree(EXAMPLE, tmp_path / "film")
    loaded = load_film(tmp_path / "film")
    again = Film.model_validate(loaded.film.model_dump(mode="json"))
    assert again == loaded.film


def test_source_needs_url_or_path() -> None:
    with pytest.raises(ValidationError, match="url or a path"):
        Source(id="s")
    assert Source(id="s", path="notes.md").path == "notes.md"


def test_claim_evidence_rules() -> None:
    for evidence in ("sourced", "measured", "trace"):
        with pytest.raises(ValidationError, match="needs a source"):
            Claim.model_validate({"id": "C", "text": "t", "evidence": evidence})
    for evidence in ("computed", "simulated", "hypothetical", "illustrative"):
        Claim.model_validate({"id": "C", "text": "t", "evidence": evidence})


def test_shot_requires_landscape_framing() -> None:
    base = {
        "id": "S1",
        "intent": "i",
        "renderer": "godot",
        "scene": "s.tscn",
        "evidence": "illustrative",
        "frames": 10,
        "framings": {"9:16": {"camera": "tall"}},
    }
    with pytest.raises(ValidationError, match='"16:9"'):
        Shot.model_validate(base)
    with pytest.raises(ValidationError):
        Shot.model_validate({**base, "framings": {"4:3": {"camera": "x"}}})
    with pytest.raises(ValidationError):
        Shot.model_validate({**base, "renderer": "unity"})


def test_expectation_keeps_scalar_types() -> None:
    assert Expectation(entity="e:x", field="up", equals=True).equals is True
    assert Expectation(entity="e:x", field="n", equals=3).equals == 3
    assert Expectation(entity="e:x", field="active_span", equals=None).equals is None


def test_builtin_profiles() -> None:
    assert {n: (p.width, p.height, p.aspect) for n, p in PROFILES.items()} == {
        "preview": (1280, 720, "16:9"),
        "production": (1920, 1080, "16:9"),
        "social": (1080, 1920, "9:16"),
        "archival": (1920, 1080, "16:9"),
    }
    assert PROFILES["preview"].quality == "draft"
