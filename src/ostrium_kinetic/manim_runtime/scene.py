# pyright: reportMissingImports=false, reportMissingModuleSource=false
"""`KineticScene`: the base class of every Manim shot scene (docs/runtime-api.md, "Manim
runtime"). A shot scene module defines `class Shot(KineticScene)` with two methods:

    build(self)  called once: create mobjects, register persistent labels and tracks
    frame(self)  called before every frame, with self.k at that frame: set positions, sizes,
                 colours and text as a pure function of self.k (never of earlier frames)

`construct()` is final: it drives time frame by frame, one `wait(1/fps, frozen_frame=False)`
per frame (a frozen wait writes a single PNG in png-sequence mode, M0), records each frame's
annotation after `frame()` and before the frame is drawn, and checks that Manim wrote exactly
one image per frame.

Manim needs LaTeX for MathTex, the default DecimalNumber and numbered Axes; Kinetic machines
have none, so scenes use `self.text()` (Pango `Text` with an explicit, installed font).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import manimpango
from manim import MarkupText, Scene, Text, config

from ostrium_kinetic.manim_runtime.kinetic import Kinetic, RuntimeFailure

DEFAULT_FONTS = {"sans": "Noto Sans", "mono": "DejaVu Sans Mono"}
_PROBE_FONT_SIZE = 100


class FontMissing(RuntimeFailure):
    def __init__(self, font: str) -> None:
        super().__init__(
            f"font {font!r} is not installed (Pango would silently substitute a serif);"
            " install it or choose another in the shot's style tokens",
            3,
        )


_installed_fonts: set[str] | None = None
_cap_heights: dict[str, float] = {}


def font_installed(font: str) -> bool:
    global _installed_fonts
    if _installed_fonts is None:
        _installed_fonts = set(manimpango.list_fonts())
    return font in _installed_fonts


class KineticScene(Scene):
    """Base class of a Manim shot scene. The runtime sets `k` before `render()`."""

    k: Kinetic
    on_frame: Callable[[dict[str, Any]], None] | None = None  # annotation sink (run.py)

    # ------------------------------------------------------------------ scene interface

    def build(self) -> None:
        """Create the shot's mobjects. Override."""

    def frame(self) -> None:
        """Update mobjects for self.k.frame / self.k.tick. Override."""

    # ------------------------------------------------------------------ helpers

    def font(self, role: str = "sans") -> str:
        """The font for a role ("sans", "mono") from style tokens `font_<role>`, checked to
        be installed: a missing font must fail, not fall back to a serif."""
        name = str(self.k.style(f"font_{role}", DEFAULT_FONTS.get(role, DEFAULT_FONTS["sans"])))
        if not font_installed(name):
            raise FontMissing(name)
        return name

    def text(
        self,
        content: str,
        size: float,
        *,
        role: str = "sans",
        color: str = "#FFFFFF",
        weight: str = "NORMAL",
        font: str | None = None,
    ) -> Text:
        """A Pango `Text` whose capital height is `size` pixels on a 1080-high frame, so text
        sizes are font-independent and match QA's label check (28 px minimum at 1080)."""
        name = font or self.font(role)
        if font is not None and not font_installed(font):
            raise FontMissing(font)
        return Text(
            content,
            font=name,
            font_size=self.font_size(size, name, weight),
            color=color,
            weight=weight,
        )

    def markup(
        self, content: str, size: float, *, role: str = "sans", color: str = "#FFFFFF"
    ) -> MarkupText:
        """A Pango `MarkupText` (spans for colour, weight and `font_family`, one baseline for
        the whole line) sized like `text()`. Escape `&`, `<` and `>` in data."""
        name = self.font(role)
        return MarkupText(content, font=name, font_size=self.font_size(size, name), color=color)

    def font_size(self, size: float, font: str, weight: str = "NORMAL") -> float:
        """Manim font_size that gives a capital height of `size` px at 1080."""
        key = f"{font}|{weight}"
        if key not in _cap_heights:
            probe = Text("H", font=font, font_size=_PROBE_FONT_SIZE, weight=weight)
            _cap_heights[key] = float(probe.height)
        return _PROBE_FONT_SIZE * self.k.px(size) / _cap_heights[key]

    # ------------------------------------------------------------------ driver

    def construct(self) -> None:
        k = self.k
        self.build()
        writer = self.renderer.file_writer
        start = writer.frame_count
        step = 1 / config.frame_rate
        for f in range(k.frame_count):
            k.run_frame(f, self.frame)
            if self.on_frame is not None:
                on_screen = {id(m) for m in self.get_mobject_family_members()}
                self.on_frame(k.annotation(on_screen))
            self.wait(step, frozen_frame=False)
            written = writer.frame_count - start
            if written != f + 1:
                raise RuntimeFailure(
                    f"Manim wrote {written} images after frame {f}, expected {f + 1}", 6
                )
