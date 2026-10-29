from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import pytest

from ostrium_kinetic import canonical
from ostrium_kinetic.canonical import dumps, fraction_str, sha256_file, sha256_tree, to_fraction

# -- to_fraction ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, Fraction(0)),
        (3, Fraction(3)),
        (-7, Fraction(-7)),
        ("1.2", Fraction(6, 5)),
        (" 1.2 ", Fraction(6, 5)),
        ("1/3", Fraction(1, 3)),
        ("-2/4", Fraction(-1, 2)),
        ("30000/1001", Fraction(30000, 1001)),
        (0.1, Fraction(1, 10)),
        (1.2, Fraction(6, 5)),
        (2.0, Fraction(2)),
        (Fraction(5, 7), Fraction(5, 7)),
    ],
)
def test_to_fraction_accepts_exact_forms(value: object, expected: Fraction) -> None:
    assert to_fraction(value) == expected


def test_to_fraction_float_uses_shortest_repr_not_binary_value() -> None:
    assert to_fraction(0.1) == Fraction(1, 10)
    assert to_fraction(0.1) != Fraction(0.1)  # the binary value is not 1/10


def test_to_fraction_returns_fraction_instance_unchanged() -> None:
    f = Fraction(2, 3)
    assert to_fraction(f) is f


@pytest.mark.parametrize("value", [True, False])
def test_to_fraction_rejects_bool(value: bool) -> None:
    with pytest.raises(ValueError, match="boolean"):
        to_fraction(value)


@pytest.mark.parametrize(
    "value", ["", "abc", "1/0", "1.2.3", "one", None, [1], {"a": 1}, object(), float("nan")]
)
def test_to_fraction_rejects_garbage(value: object) -> None:
    with pytest.raises(ValueError):
        to_fraction(value)


def test_fraction_str() -> None:
    assert fraction_str(Fraction(4)) == "4"
    assert fraction_str(Fraction(-6, 4)) == "-3/2"
    assert fraction_str(Fraction(30000, 1001)) == "30000/1001"


# -- dumps ------------------------------------------------------------------------------


def test_dumps_sorts_keys_recursively_and_has_no_whitespace() -> None:
    value = {"b": 1, "a": {"z": [1, 2], "y": "x"}}
    assert dumps(value) == '{"a":{"y":"x","z":[1,2]},"b":1}'


def test_dumps_is_independent_of_insertion_order() -> None:
    assert dumps({"a": 1, "b": 2}) == dumps({"b": 2, "a": 1})


def test_dumps_fractions_and_paths() -> None:
    value = {"fps": Fraction(30000, 1001), "whole": Fraction(30), "p": Path("a") / "b.py"}
    assert dumps(value) == '{"fps":"30000/1001","p":"a/b.py","whole":"30"}'


def test_dumps_keeps_unicode_unescaped() -> None:
    assert dumps({"k": "µs → é"}) == '{"k":"µs → é"}'


def test_dumps_rejects_unknown_types() -> None:
    with pytest.raises(TypeError):
        dumps({"x": object()})


def test_sha256_json_matches_dumps() -> None:
    value = {"b": [1, Fraction(1, 2)], "a": None}
    assert canonical.sha256_json(value) == canonical.sha256_bytes(dumps(value).encode("utf-8"))


# -- file and tree hashes ---------------------------------------------------------------


def test_sha256_file_crlf_equals_lf(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"x = 1\ny = 2\n")
    crlf.write_bytes(b"x = 1\r\ny = 2\r\n")
    assert sha256_file(lf) == sha256_file(crlf)


def test_sha256_file_detects_content_change(tmp_path: Path) -> None:
    path = tmp_path / "f.txt"
    path.write_bytes(b"a\n")
    before = sha256_file(path)
    path.write_bytes(b"b\n")
    assert sha256_file(path) != before


def _make_tree(root: Path, newline: bytes = b"\n") -> None:
    (root / "pkg").mkdir(parents=True)
    (root / "a.py").write_bytes(b"A = 1" + newline)
    (root / "pkg" / "b.py").write_bytes(b"B = 2" + newline)


def test_sha256_tree_is_stable_and_line_ending_independent(tmp_path: Path) -> None:
    _make_tree(tmp_path / "lf")
    _make_tree(tmp_path / "crlf", b"\r\n")
    assert sha256_tree(tmp_path / "lf") == sha256_tree(tmp_path / "crlf")
    assert sha256_tree(tmp_path / "lf") == sha256_tree(tmp_path / "lf")


def test_sha256_tree_sees_renames_and_edits(tmp_path: Path) -> None:
    root = tmp_path / "t"
    _make_tree(root)
    base = sha256_tree(root)

    (root / "pkg" / "b.py").write_bytes(b"B = 3\n")
    edited = sha256_tree(root)
    assert edited != base

    (root / "pkg" / "b.py").rename(root / "pkg" / "c.py")
    assert sha256_tree(root) != edited


def test_sha256_tree_orders_by_posix_string_not_path(tmp_path: Path) -> None:
    """Path comparison is case-insensitive on Windows; the hash must use str order."""
    root = tmp_path / "t"
    names = ["b2.py", "a.py", "C/x.py", "B.py", "c.py"]
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(name.encode() + b"\n")
    expected_order = ["B.py", "C/x.py", "a.py", "b2.py", "c.py"]
    assert sorted(names) == expected_order
    entries = [[n, sha256_file(root / n)] for n in expected_order]
    assert sha256_tree(root) == canonical.sha256_json(entries)
    shuffled = [root / n for n in reversed(names)]
    assert sha256_tree(root, shuffled) == canonical.sha256_json(entries)


def test_sha256_tree_with_explicit_file_list(tmp_path: Path) -> None:
    root = tmp_path / "t"
    _make_tree(root)
    (root / "notes.txt").write_bytes(b"ignored\n")
    only_py = sorted(root.rglob("*.py"))
    with_notes = sha256_tree(root)
    assert sha256_tree(root, only_py) != with_notes
    (root / "notes.txt").write_bytes(b"still ignored\n")
    assert sha256_tree(root, sorted(root.rglob("*.py"))) == sha256_tree(root, only_py)


def test_write_text_lf_writes_lf_only(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "out.json"
    canonical.write_text_lf(path, "a\nb\n")
    assert path.read_bytes() == b"a\nb\n"


def test_dumps_rejects_nan_and_infinity() -> None:
    with pytest.raises(ValueError):
        canonical.dumps({"x": float("nan")})
    with pytest.raises(ValueError):
        canonical.dumps([float("inf")])
