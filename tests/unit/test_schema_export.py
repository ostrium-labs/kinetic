"""Committed JSON Schemas must match the models they are generated from."""

from __future__ import annotations

import json

from ostrium_kinetic.schema_export import SCHEMA_DIR, export_schemas


def test_committed_schemas_match_models() -> None:
    exported = export_schemas()
    assert sorted(exported) == [
        "film.schema.json",
        "render-plan.schema.json",
        "span.schema.json",
        "trace-event.schema.json",
        "trace-meta.schema.json",
    ]
    for name, text in exported.items():
        committed = (SCHEMA_DIR / name).read_bytes().decode("utf-8")
        assert committed == text, f"{name} is stale; run `kinetic schema`"


def test_schemas_are_deterministic_json() -> None:
    first, second = export_schemas(), export_schemas()
    assert first == second
    for text in first.values():
        assert text.endswith("}\n")
        json.loads(text)


def test_film_schema_shape() -> None:
    film = json.loads(export_schemas()["film.schema.json"])
    assert film["additionalProperties"] is False
    assert set(film["required"]) == {"kinetic", "id", "title", "sequences"}
    assert "Evidence" in film["$defs"]
