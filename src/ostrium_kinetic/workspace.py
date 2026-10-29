"""Where a Kinetic run lives: the workspace root, its kinetic.toml, and its cache directory."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_NAME = "kinetic.toml"
DEFAULT_CACHE = ".kinetic"


@dataclass(frozen=True)
class Workspace:
    """`root` is the nearest ancestor of the start path containing kinetic.toml, or the
    current directory when there is none. All generated files go under `cache_dir`."""

    root: Path
    config_path: Path | None
    config: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def find(cls, start: Path | None = None) -> Workspace:
        here = (start or Path.cwd()).resolve()
        if here.is_file():
            here = here.parent
        for candidate in (here, *here.parents):
            path = candidate / CONFIG_NAME
            if path.is_file():
                with path.open("rb") as fh:
                    return cls(candidate, path, tomllib.load(fh))
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
