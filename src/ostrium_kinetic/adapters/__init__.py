"""Renderer adapters: run a renderer for one shot job and verify what it produced."""

from __future__ import annotations

from ostrium_kinetic.adapters.godot import GodotAdapter
from ostrium_kinetic.job import AdapterError, RendererAdapter

__all__ = ["AdapterError", "GodotAdapter", "RendererAdapter", "get_adapter"]


def get_adapter(renderer: str) -> RendererAdapter:
    if renderer == "godot":
        return GodotAdapter()
    raise AdapterError(f"no adapter for renderer {renderer!r} yet")
