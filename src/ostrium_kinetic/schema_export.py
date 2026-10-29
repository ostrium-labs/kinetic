"""JSON Schemas generated from the contract models. Never edit the files in schemas/ by hand."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ostrium_kinetic.contract.models import Film
from ostrium_kinetic.plan.compiler import RenderPlan
from ostrium_kinetic.sim.model import Span, TraceEvent, TraceMeta

SCHEMA_DIR = Path(__file__).parent / "schemas"

_MODELS: dict[str, type[BaseModel]] = {
    "film.schema.json": Film,
    "trace-event.schema.json": TraceEvent,
    "span.schema.json": Span,
    "trace-meta.schema.json": TraceMeta,
    "render-plan.schema.json": RenderPlan,
}


def export_schemas() -> dict[str, str]:
    """File name -> schema text (sorted keys, 2-space indent, trailing newline)."""
    return {
        name: json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"
        for name, model in _MODELS.items()
    }
