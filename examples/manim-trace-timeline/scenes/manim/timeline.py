"""The failover trace as a timeline: one lane per packet, span bars growing in sim time.

Every position and number comes from the trace through self.k: lanes from the spans (planned
in build()), bar extents from begin/end/planned_end ticks clipped to the current tick, captions
from span attrs and state, the cursor and readout from k.tick, markers from signals.
Layout is on a 1080-high pixel grid (k.px); all text is Pango (no LaTeX).
"""

from __future__ import annotations

from html import escape
from typing import Any

import numpy as np
from manim import DashedVMobject, Line, Rectangle, VGroup, VMobject

from ostrium_kinetic.manim_runtime.scene import KineticScene

# Palette
BACKGROUND = "#0E1116"  # the runtime default (style token `background`)
TEXT = "#E6EDF3"
MUTED = "#8B98A5"
GRID = "#262E38"
TRACK = "#3A4552"
FLIGHT = "#58A6FF"
DONE = "#3FB950"
LOST = "#F85149"
EVENT = "#D29922"

# Grid (px on a 1080-high frame)
MARGIN_X = 144
TOP = 96
LABEL_SIZE = 30  # capital height of every registered label
AXIS_SIZE = 28
LANE_TOP = 312
LANE_PITCH = 184
CAPTION_TO_BAR = 64
BAR_H = 48
AXIS_Y = 888
CURSOR_TOP = 264
READOUT_Y = 208  # top of the cursor readout
AXIS_STEP_MS = 500
EVENT_HOLD_TICKS = 450  # the readout names a signal for this long after it fires
MIN_BAR_PX = 4


def _short(entity: str) -> str:
    return entity.split(":", 1)[1]


class Shot(KineticScene):
    def build(self) -> None:
        k = self.k
        self.fw = 1080 * k.width / k.height  # frame width in grid px
        self.x0, self.x1 = MARGIN_X, self.fw - MARGIN_X
        self.t0, self.t1 = k.tick_start, k.tick_end
        self.lanes = [s for s in sorted(k.spans, key=lambda s: s.begin_tick)]
        self.signals = k.signals_in(self.t0 - 1, self.t1)
        self._texts: dict[tuple[Any, ...], VMobject] = {}

        # Badge: evidence class and time-scale label from the job (never hard-coded).
        parts = [p for p in (k.evidence.upper(), k.time_label) if p]
        if parts:
            badge = self.text("  ·  ".join(parts), LABEL_SIZE, color=MUTED)
            self.place(badge, MARGIN_X, TOP)
            self.add(badge)
            k.label(" · ".join(parts), badge)

        # Axis: baseline, ticks every 500 ms, numerals, unit; lane guides.
        axis = VGroup(self.hline(self.x0, self.x1, AXIS_Y, MUTED, 2))
        for ms in range(0, self.t1 - self.t0 + 1, AXIS_STEP_MS):
            tick = self.t0 + ms
            x = self.x(tick)
            axis.add(self.vline(x, AXIS_Y, AXIS_Y + 12, MUTED, 2))
            axis.add(self.vline(x, CURSOR_TOP, AXIS_Y, GRID, 1))
            num = self.text(str(tick), AXIS_SIZE, role="mono", color=MUTED)
            self.place(num, x, AXIS_Y + 28, anchor="top")
            axis.add(num)
        unit = self.text("sim time, ms", AXIS_SIZE, color=MUTED)
        self.place(unit, self.x1, AXIS_Y + 88, anchor="top-right")
        axis.add(unit)
        self.add(axis)
        self.dynamic = VGroup()
        self.add(self.dynamic)

    # ------------------------------------------------------------------ per frame

    def frame(self) -> None:
        k = self.k
        self.dynamic.remove(*self.dynamic.submobjects)
        now = k.tick

        # Signals that have fired: a marker line across the lanes and a diamond on the axis.
        for sig in self.signals:
            if sig["tick"] <= now:
                x = self.x(sig["tick"])
                line = self.vline(x, CURSOR_TOP, AXIS_Y, EVENT, 2)
                self.dynamic.add(DashedVMobject(line, num_dashes=28, dashed_ratio=0.5))

        # Cursor (under the lanes, so caption plates and bars cover it) and its readout:
        # the tick, plus the signal that fired most recently.
        x = self.x(now)
        self.dynamic.add(self.vline(x, CURSOR_TOP, AXIS_Y, TEXT, 3))
        for i, span in enumerate(self.lanes):
            self.lane(i, span, now)

        readout = f'<span font_family="{self.font("mono")}">{now} ms</span>'
        recent = [s for s in self.signals if 0 <= now - s["tick"] < EVENT_HOLD_TICKS]
        if recent:
            name = escape(recent[-1]["name"].replace(".", " "))
            readout += f'   <span foreground="{EVENT}">{name}</span>'
        text = self.cached_markup(readout, TEXT)
        half = text.width / 2 / k.px(1)
        cx = min(max(x, self.x0 + half), self.x1 - half)
        self.place(text, cx, READOUT_Y, anchor="top")
        self.dynamic.add(self.backdrop(text), text)
        k.label(f"{now} ms", text)

    def lane(self, i: int, span: Any, now: int) -> None:
        k = self.k
        entity = span.entity
        top = LANE_TOP + i * LANE_PITCH
        bar_y0 = top + CAPTION_TO_BAR
        bar_y1 = bar_y0 + BAR_H
        begin, end = span.begin_tick, span.end_tick
        planned = span.planned_end_tick or end
        started = now >= begin
        ended = now >= end
        lost = ended and span.outcome != "completed"
        state = k.state(entity)

        # Caption: entity, route, and what the trace says about it now.
        mono = self.font("mono")  # Noto Sans has no arrow glyph; DejaVu Sans Mono does
        route = (
            f"{escape(_short(span.attrs['from']))}"
            f' <span font_family="{mono}">→</span> '
            f"{escape(_short(span.attrs['to']))} via {escape(str(span.attrs['via']))}"
        )
        caption = (
            f'<span font_family="{mono}">{escape(entity)}</span>'
            f'   <span foreground="{MUTED}">{route}</span>'
        )
        if lost:
            caption += (
                f'   <span foreground="{LOST}">{escape(str(state.get("status", "")))}'
                f" at {end} ms</span>"
                f'<span foreground="{MUTED}">, planned {planned} ms</span>'
            )
        elif ended:
            status = escape(str(state.get("status", "")))
            caption += f'   <span foreground="{DONE}">{status} at {end} ms</span>'
        elif started:
            caption += f'   <span foreground="{FLIGHT}">sent at {begin} ms</span>'
        text = self.cached_markup(caption, TEXT if started else MUTED)
        self.place(text, self.x0, top, anchor="top-left")
        self.dynamic.add(self.backdrop(text), text)
        k.label(entity, text)

        bar = VGroup()
        if started:
            # Planned interval: an outline from begin to planned end (known at begin, from the
            # span); the actual interval fills it up to min(now, end).
            if not lost:
                self.dynamic.add(self.box(self.x(begin), self.x(planned), bar_y0, bar_y1, TRACK, 2))
            stop = min(now, end)
            x_end = max(self.x(stop), self.x(begin) + MIN_BAR_PX)
            colour = LOST if lost else (DONE if ended else FLIGHT)
            bar = self.box(self.x(begin), x_end, bar_y0, bar_y1, colour, 0, fill=colour)
            self.dynamic.add(bar)
            if lost:
                ghost = self.box(self.x(end), self.x(planned), bar_y0, bar_y1, LOST, 2)
                length = self.x(planned) - self.x(end)
                self.dynamic.add(DashedVMobject(ghost, num_dashes=max(8, int(length / 9))))
                self.dynamic.add(self.vline(self.x(planned), bar_y0 - 8, bar_y1 + 8, LOST, 3))
        k.track(entity, bar)

    # ------------------------------------------------------------------ geometry helpers

    def x(self, tick: int) -> float:
        return self.x0 + (self.x1 - self.x0) * (tick - self.t0) / (self.t1 - self.t0)

    def p(self, x: float, y: float) -> np.ndarray:
        s = self.k.px(1)
        return np.array([(x - self.fw / 2) * s, (540 - y) * s, 0.0])

    def hline(self, x0: float, x1: float, y: float, colour: str, width: float) -> Line:
        return Line(self.p(x0, y), self.p(x1, y), color=colour, stroke_width=width)

    def vline(self, x: float, y0: float, y1: float, colour: str, width: float) -> Line:
        return Line(self.p(x, y0), self.p(x, y1), color=colour, stroke_width=width)

    def box(
        self,
        x0: float,
        x1: float,
        y0: float,
        y1: float,
        stroke: str,
        width: float,
        fill: str | None = None,
    ) -> Rectangle:
        s = self.k.px(1)
        rect = Rectangle(
            width=(x1 - x0) * s,
            height=(y1 - y0) * s,
            color=stroke,
            stroke_width=width,
            fill_color=fill or stroke,
            fill_opacity=1.0 if fill else 0.0,
        )
        return rect.move_to(self.p((x0 + x1) / 2, (y0 + y1) / 2))

    def place(self, mob: VMobject, x: float, y: float, anchor: str = "top-left") -> None:
        """Put a mobject's ink box corner/edge (top-left, top, top-right) at grid (x, y)."""
        s = self.k.px(1)
        w, h = mob.width / s, mob.height / s
        left = {"top-left": x, "top": x - w / 2, "top-right": x - w}[anchor]
        mob.move_to(self.p(left + w / 2, y + h / 2))

    def backdrop(self, mob: VMobject) -> Rectangle:
        """A background-coloured plate behind text, so marker and cursor lines pass under it."""
        pad = self.k.px(12)
        plate = Rectangle(
            width=mob.width + 2 * pad,
            height=mob.height + 2 * pad,
            stroke_width=0,
            fill_color=BACKGROUND,
            fill_opacity=1.0,
        )
        return plate.move_to(mob.get_center())

    def cached_markup(self, content: str, colour: str) -> VMobject:
        """Pango shaping is the slow part of a frame, so each distinct string is shaped once
        and the same mobject is re-placed on later frames (copying it costs about as much).
        A string must therefore appear at most once per frame."""
        key = (content, colour)
        if key not in self._texts:
            self._texts[key] = self.markup(content, LABEL_SIZE, color=colour)
        return self._texts[key]
