"""Finding the workspace and reading kinetic.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from ostrium_kinetic.workspace import CONFIG_NAME, DEFAULT_CACHE, Workspace, WorkspaceError


def _ws(tmp_path: Path, text: str) -> Workspace:
    (tmp_path / CONFIG_NAME).write_text(text, "utf-8")
    (tmp_path / "film").mkdir(exist_ok=True)
    return Workspace.find(tmp_path / "film")


def test_finds_nearest_config_and_reads_it(tmp_path: Path) -> None:
    ws = _ws(tmp_path, '[cache]\ndir = "c"\n[tools]\ngodot = "C:/g/godot.exe"\n')
    assert ws.root == tmp_path.resolve()
    assert ws.cache_dir == tmp_path.resolve() / "c"
    assert ws.tools == {"godot": "C:/g/godot.exe"}


def test_empty_config_uses_defaults(tmp_path: Path) -> None:
    ws = _ws(tmp_path, "")
    assert ws.cache_dir == tmp_path.resolve() / DEFAULT_CACHE
    assert ws.tools == {}


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("[cache\ndir = 'x'\n", "invalid TOML"),
        ("cache = 3\n", r"\[cache\] must be a table"),
        ("[cache]\ndir = 5\n", r"\[cache\] dir must be a string, got 5"),
        ("tools = ['godot']\n", r"\[tools\] must be a table"),
        ("[tools]\ngodot = 4.7\n", r"\[tools\] godot must be a string"),
        ("[tools]\ngodot = { path = 'x' }\n", r"\[tools\] godot must be a string"),
    ],
)
def test_malformed_config_raises_workspace_error(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(WorkspaceError, match=match) as info:
        _ws(tmp_path, text)
    assert str(tmp_path / CONFIG_NAME) in str(info.value)
    assert not isinstance(info.value, (ValueError, TypeError))
