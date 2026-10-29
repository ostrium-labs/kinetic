"""Where a Kinetic run lives: the workspace root, its kinetic.toml, and its cache directory."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "kinetic.toml"
DEFAULT_CACHE = ".kinetic"


class WorkspaceError(Exception):
    """kinetic.toml cannot be read, is not valid TOML, or has values of the wrong type."""


def _check_config(path: Path, config: dict[str, Any]) -> None:
    cache = config.get("cache", {})
    if not isinstance(cache, dict):
        raise WorkspaceError(f"{path}: [cache] must be a table")
    if "dir" in cache and not isinstance(cache["dir"], str):
        raise WorkspaceError(f"{path}: [cache] dir must be a string, got {cache['dir']!r}")
    tools = config.get("tools", {})
    if not isinstance(tools, dict):
        raise WorkspaceError(f"{path}: [tools] must be a table")
    for name, value in tools.items():
        if not isinstance(value, str):
            raise WorkspaceError(
                f"{path}: [tools] {name} must be a string (an executable path), got {value!r}"
            )


def load_config(path: Path) -> dict[str, Any]:
    """Read and type-check kinetic.toml; every problem is a WorkspaceError naming the file."""
    try:
        with path.open("rb") as fh:
            config = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise WorkspaceError(f"{path}: invalid TOML: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkspaceError(f"{path}: cannot read: {exc}") from exc
    _check_config(path, config)
    return config


@dataclass(frozen=True)
class Workspace:
    """`root` is the nearest ancestor of the start path containing kinetic.toml, or the
    current directory when there is none. All generated files go under `cache_dir`."""

    root: Path
    config_path: Path | None
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def find(cls, start: Path | None = None) -> Workspace:
        """Raises WorkspaceError when the kinetic.toml found is malformed."""
        here = (start or Path.cwd()).resolve()
        if here.is_file():
            here = here.parent
        for candidate in (here, *here.parents):
            path = candidate / CONFIG_NAME
            if path.is_file():
                return cls(candidate, path, load_config(path))
        return cls(Path.cwd().resolve(), None, {})

    @property
    def cache_dir(self) -> Path:
        configured = self.config.get("cache", {}).get("dir")
        return (self.root / configured) if configured else (self.root / DEFAULT_CACHE)

    @property
    def tools(self) -> dict[str, str]:
        """[tools] table: tool name -> executable path, e.g. godot = "C:/.../Godot_console.exe"."""
        return {str(k): str(v) for k, v in self.config.get("tools", {}).items()}

    def cache(self, *parts: str) -> Path:
        path = self.cache_dir.joinpath(*parts)
        path.mkdir(parents=True, exist_ok=True)
        return path
