"""Exact numbers and canonical JSON: the two things every hash in Kinetic depends on."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from fractions import Fraction
from pathlib import Path
from typing import Annotated, Any

from pydantic import BeforeValidator, PlainSerializer, WithJsonSchema


def to_fraction(value: Any) -> Fraction:
    """Parse an exact rational from int, decimal string, "a/b" string or Fraction.

    Floats are converted through their shortest repr ("0.1" -> 1/10), never through
    their binary value, so YAML like `1.2` means exactly 6/5.
    """
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise ValueError("expected a number, got a boolean")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        return Fraction(repr(value))
    if isinstance(value, str):
        try:
            return Fraction(value.strip())
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(f"not an exact number: {value!r}") from exc
    raise ValueError(f"not an exact number: {value!r}")


def fraction_str(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


# Pydantic type for exact rationals; serialised as "a/b" (or "a") strings.
Exact = Annotated[
    Fraction,
    BeforeValidator(to_fraction),
    PlainSerializer(fraction_str, return_type=str),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "integer"},
                {"type": "number"},
                {"type": "string", "pattern": r"^-?\d+(\.\d+)?(/\d+)?$"},
            ],
            "description": "Exact rational: integer, decimal, or 'a/b' string.",
        }
    ),
]


def _default(value: Any) -> Any:
    if isinstance(value, Fraction):
        return fraction_str(value)
    if isinstance(value, Path):
        return value.as_posix()
    raise TypeError(f"not JSON serialisable: {type(value).__name__}")


def dumps(value: Any) -> str:
    """Canonical JSON: sorted keys, no whitespace, UTF-8, Fractions as strings.

    NaN and infinity are rejected: they are not JSON and would make hashes ambiguous."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
        default=_default,
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(dumps(value).encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Hash a source file with line endings normalised, so a checkout with CRLF and one
    with LF produce the same key."""
    data = path.read_bytes().replace(b"\r\n", b"\n")
    return sha256_bytes(data)


def sha256_tree(root: Path, files: Iterable[Path] | None = None) -> str:
    """Hash a directory tree (relative path + normalised content of every file)."""
    paths = sorted(files if files is not None else (p for p in root.rglob("*") if p.is_file()))
    entries = [[p.relative_to(root).as_posix(), sha256_file(p)] for p in paths]
    return sha256_json(entries)


def write_text_lf(path: Path, text: str) -> None:
    """Write UTF-8 text with LF line endings on every platform."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
