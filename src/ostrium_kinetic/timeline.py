"""Film time. Integer frame indices are authoritative; seconds are derived, never accumulated."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction


def frame_to_seconds(frame: int, fps: Fraction) -> Fraction:
    if frame < 0:
        raise ValueError("frame index must be >= 0")
    return Fraction(frame) / fps


def seconds_to_frame(seconds: Fraction, fps: Fraction) -> int:
    """The frame on screen at `seconds` (floor)."""
    return math.floor(seconds * fps)


def seconds_to_ticks(seconds: Fraction, tick_rate: Fraction) -> int:
    """`tick_rate` is seconds per tick. Raises if `seconds` is not a whole number of ticks."""
    ticks = seconds / tick_rate
    if ticks.denominator != 1:
        raise ValueError(f"{seconds} s is not a whole number of ticks at {tick_rate} s/tick")
    return ticks.numerator


@dataclass(frozen=True)
class ShotClock:
    """Maps a shot's frames onto a window of simulation ticks, linearly.

    Frame f (0 <= f < frame_count) shows sim tick
        start_tick + floor((end_tick - start_tick) * f / frame_count)
    using exact integer arithmetic, so every renderer samples the same tick.
    """

    frame_count: int
    fps: Fraction
    start_tick: int
    end_tick: int
    tick_rate: Fraction

    def __post_init__(self) -> None:
        if self.frame_count <= 0:
            raise ValueError("frame_count must be > 0")
        if self.end_tick <= self.start_tick:
            raise ValueError("sim window must have end > start")

    def tick_at(self, frame: int) -> int:
        if not 0 <= frame < self.frame_count:
            raise ValueError(f"frame {frame} outside shot of {self.frame_count} frames")
        span = self.end_tick - self.start_tick
        return self.start_tick + (span * frame) // self.frame_count

    @property
    def film_seconds(self) -> Fraction:
        return Fraction(self.frame_count) / self.fps

    @property
    def sim_seconds(self) -> Fraction:
        return (self.end_tick - self.start_tick) * self.tick_rate

    @property
    def speed(self) -> Fraction:
        """Simulated seconds shown per film second. 1 means real time."""
        return self.sim_seconds / self.film_seconds
