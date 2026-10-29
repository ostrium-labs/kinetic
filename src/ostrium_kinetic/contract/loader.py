"""Reading film.yaml (and its claims file) into validated models with readable errors."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from ostrium_kinetic.contract.models import Claim, Film

FILM_FILE = "film.yaml"

_CLAIMS = TypeAdapter(list[Claim])


class FilmLoadError(Exception):
    """film.yaml (or a file it refers to) could not be read or does not match the contract."""


@dataclass
class LoadedFilm:
    film: Film
    film_dir: Path
    film_path: Path
    claims: list[Claim]  # resolved, whether inline or from a claims file


def read_yaml(path: Path) -> Any:
    """Safe, pure-Python YAML 1.2. Unquoted decimals arrive as floats; `Exact` fields turn
    them into rationals via their shortest repr, so `1.2` means exactly 6/5."""
    yaml = YAML(typ="safe", pure=True)
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.load(fh)
    except OSError as exc:
        raise FilmLoadError(f"{path}: cannot read: {exc}") from exc
    except YAMLError as exc:
        raise FilmLoadError(f"{path}: invalid YAML: {exc}") from exc


def _loc(loc: tuple[int | str, ...]) -> str:
    parts: list[str] = []
    for i, item in enumerate(loc):
        if i == 1 and loc[0] == "claims" and item in ("inline", "file"):
            continue  # discriminator tag of Film.claims, not a real path element
        if isinstance(item, int):
            parts.append(f"[{item}]")
        else:
            parts.append(("." if parts else "") + item)
    return "".join(parts) or "(root)"


def format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path}: {exc.error_count()} error(s)"]
    for err in exc.errors():
        message = err["msg"].removeprefix("Value error, ")
        lines.append(f"  {_loc(err['loc'])}: {message}")
    return "\n".join(lines)


def load_film(path: Path) -> LoadedFilm:
    """Load a film from its directory (film.yaml inside) or from a YAML file."""
    path = Path(path)
    film_path = (path / FILM_FILE if path.is_dir() else path).resolve()
    if not film_path.is_file():
        raise FilmLoadError(f"{film_path}: no such film file")
    raw = read_yaml(film_path)
    if not isinstance(raw, dict):
        raise FilmLoadError(f"{film_path}: expected a mapping at the top level")
    try:
        film = Film.model_validate(raw)
    except ValidationError as exc:
        raise FilmLoadError(format_validation_error(film_path, exc)) from exc

    film_dir = film_path.parent
    if isinstance(film.claims, str):
        claims_path = film_dir / film.claims
        if not claims_path.is_file():
            raise FilmLoadError(f"{film_path}: claims: file not found: {film.claims}")
        raw_claims = read_yaml(claims_path)
        if isinstance(raw_claims, dict) and "claims" in raw_claims:
            raw_claims = raw_claims["claims"]
        try:
            claims = _CLAIMS.validate_python(raw_claims if raw_claims is not None else [])
        except ValidationError as exc:
            raise FilmLoadError(format_validation_error(claims_path, exc)) from exc
    else:
        claims = list(film.claims)
    return LoadedFilm(film=film, film_dir=film_dir, film_path=film_path, claims=claims)
