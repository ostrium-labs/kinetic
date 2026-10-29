"""Film contract v0.1: the typed shape of film.yaml. JSON Schema is generated from these."""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, model_validator

from ostrium_kinetic.canonical import Exact

Aspect = Literal["16:9", "9:16"]
Id = Annotated[str, Field(min_length=1)]
Scalar = str | int | float | bool | None


class Evidence(StrEnum):
    """How we know what a shot or claim shows."""

    TRACE = "trace"  # recorded from a real system
    MEASURED = "measured"  # a measurement we (or a source) made
    SOURCED = "sourced"  # stated by a cited source
    COMPUTED = "computed"  # derived arithmetically from other claims
    SIMULATED = "simulated"  # produced by a deterministic simulation
    HYPOTHETICAL = "hypothetical"  # a design target or what-if
    ILLUSTRATIVE = "illustrative"  # a picture, carries no quantities


# Evidence classes that must cite a source.
SOURCE_REQUIRED = frozenset({Evidence.SOURCED, Evidence.MEASURED, Evidence.TRACE})
# Evidence classes whose quantities an illustrative shot must not present.
QUANTITATIVE = frozenset({Evidence.MEASURED, Evidence.SOURCED, Evidence.COMPUTED, Evidence.TRACE})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Source(_Model):
    id: Id
    url: str | None = None
    path: str | None = None
    retrieved: dt.date | None = None
    sha256: str | None = None

    @model_validator(mode="after")
    def _url_or_path(self) -> Source:
        if not self.url and not self.path:
            raise ValueError("a source needs a url or a path")
        return self


class Claim(_Model):
    id: Id
    text: Annotated[str, Field(min_length=1)]
    evidence: Evidence
    source: str | None = None  # Source id
    anchor: str | None = None  # where in the source, e.g. a heading or line
    notes: str | None = None

    @model_validator(mode="after")
    def _source_required(self) -> Claim:
        if self.evidence in SOURCE_REQUIRED and not self.source:
            raise ValueError(f"a {self.evidence.value} claim needs a source")
        return self


class Assumption(_Model):
    id: Id
    text: Annotated[str, Field(min_length=1)]


class SimRef(_Model):
    module: Annotated[str, Field(min_length=1)]  # path relative to the film directory
    params: dict[str, Any] = Field(default_factory=dict)
    seed: Annotated[int, Field(ge=0)]


class TimeMap(_Model):
    """Linear map from a shot's frames onto [sim_start_s, sim_end_s] of simulated time."""

    mode: Literal["linear"] = "linear"
    sim_start_s: Exact
    sim_end_s: Exact
    label: str | None = None  # required on screen when the speed is not 1


class Framing(_Model):
    camera: Annotated[str, Field(min_length=1)]


class Expectation(_Model):
    """`field` is a state field ("status"), `active_span.<attr>` ("active_span.via"), or
    `active_span` itself, where `equals: null` means "no active span" and a string is
    compared with the active span's id."""

    entity: Annotated[str, Field(min_length=1)]
    field: Annotated[str, Field(min_length=1)]
    equals: Scalar


class Checkpoint(_Model):
    at_frame: Annotated[int, Field(ge=0)]
    expect: Annotated[list[Expectation], Field(min_length=1)]


class Shot(_Model):
    id: Id
    intent: Annotated[str, Field(min_length=1)]
    renderer: Literal["godot", "blender", "manim"]
    scene: Annotated[str, Field(min_length=1)]  # path relative to the film directory
    evidence: Evidence
    claims: list[str] = Field(default_factory=list)
    frames: Annotated[int, Field(gt=0)]
    sim: SimRef | None = None
    timemap: TimeMap | None = None
    framings: dict[Aspect, Framing]
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    narration: str | None = None  # NarrationSegment id
    variant_calm: bool = False

    @model_validator(mode="after")
    def _landscape_framing(self) -> Shot:
        if "16:9" not in self.framings:
            raise ValueError('framings must include "16:9"')
        return self


class Sequence(_Model):
    id: Id
    title: Annotated[str, Field(min_length=1)]
    shots: Annotated[list[Shot], Field(min_length=1)]


class NarrationSegment(_Model):
    id: Id
    text: Annotated[str, Field(min_length=1)]


def _claims_kind(value: Any) -> str:
    return "file" if isinstance(value, str) else "inline"


# Inline list of claims, or a path (relative to the film directory) to a YAML list of claims.
ClaimsField = Annotated[
    Annotated[list[Claim], Tag("inline")] | Annotated[str, Tag("file")],
    Discriminator(_claims_kind),
]


class Film(_Model):
    kinetic: Literal["0.1"]
    id: Id
    title: Annotated[str, Field(min_length=1)]
    fps: Exact = Fraction(30)
    sources: list[Source] = Field(default_factory=list)
    claims: ClaimsField = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    style: str | None = None
    sequences: Annotated[list[Sequence], Field(min_length=1)]
    narration: list[NarrationSegment] = Field(default_factory=list)

    @model_validator(mode="after")
    def _positive_fps(self) -> Film:
        if self.fps <= 0:
            raise ValueError("fps must be > 0")
        return self

    def shots(self) -> list[tuple[Sequence, Shot]]:
        """Every shot in film order, with its sequence."""
        return [(seq, shot) for seq in self.sequences for shot in seq.shots]


class Profile(_Model):
    name: str
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]
    aspect: Aspect
    quality: Literal["draft", "high"]


PROFILES: dict[str, Profile] = {
    p.name: p
    for p in (
        Profile(name="preview", width=1280, height=720, aspect="16:9", quality="draft"),
        Profile(name="production", width=1920, height=1080, aspect="16:9", quality="high"),
        Profile(name="social", width=1080, height=1920, aspect="9:16", quality="high"),
        Profile(name="archival", width=1920, height=1080, aspect="16:9", quality="high"),
    )
}
