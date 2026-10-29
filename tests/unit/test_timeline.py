from __future__ import annotations

import itertools
from fractions import Fraction

import pytest
from hypothesis import given
from hypothesis import strategies as st

from ostrium_kinetic.timeline import (
    ShotClock,
    frame_to_seconds,
    seconds_to_frame,
    seconds_to_ticks,
)

FPS_VALUES = [
    Fraction(24),
    Fraction(25),
    Fraction(30),
    Fraction(60),
    Fraction(24000, 1001),
    Fraction(30000, 1001),
    Fraction(60000, 1001),
]
MS = Fraction(1, 1000)

# -- frames <-> seconds -----------------------------------------------------------------


def test_frame_to_seconds_exact() -> None:
    assert frame_to_seconds(0, Fraction(30)) == 0
    assert frame_to_seconds(45, Fraction(30)) == Fraction(3, 2)
    assert frame_to_seconds(30000, Fraction(30000, 1001)) == Fraction(1001)


def test_frame_to_seconds_rejects_negative_frame() -> None:
    with pytest.raises(ValueError):
        frame_to_seconds(-1, Fraction(30))


def test_seconds_to_frame_floors() -> None:
    fps = Fraction(30)
    assert seconds_to_frame(Fraction(1, 30), fps) == 1
    assert seconds_to_frame(Fraction(1, 30) - Fraction(1, 10**9), fps) == 0
    assert seconds_to_frame(Fraction(7, 2), fps) == 105


@pytest.mark.parametrize("fps", FPS_VALUES, ids=str)
def test_frame_seconds_round_trip_known_fps(fps: Fraction) -> None:
    for frame in (0, 1, 2, 29, 30, 1000, 1001, 30_000, 107_892):
        assert seconds_to_frame(frame_to_seconds(frame, fps), fps) == frame


@given(frame=st.integers(min_value=0, max_value=10_000_000), fps=st.sampled_from(FPS_VALUES))
def test_frame_seconds_round_trip_property(frame: int, fps: Fraction) -> None:
    assert seconds_to_frame(frame_to_seconds(frame, fps), fps) == frame


@given(
    frame=st.integers(min_value=0, max_value=1_000_000),
    num=st.integers(min_value=1, max_value=240_000),
    den=st.integers(min_value=1, max_value=1001),
)
def test_frame_seconds_round_trip_any_rational_fps(frame: int, num: int, den: int) -> None:
    fps = Fraction(num, den)
    assert seconds_to_frame(frame_to_seconds(frame, fps), fps) == frame


def test_ntsc_rate_is_not_thirty() -> None:
    fps = Fraction(30000, 1001)
    # one hour of NTSC frames is slightly longer than 3600 s of 30 fps frames
    assert frame_to_seconds(108_000, fps) == Fraction("3603.6")
    assert seconds_to_frame(Fraction(3600), fps) == 107_892


# -- seconds -> ticks -------------------------------------------------------------------


def test_seconds_to_ticks_exact() -> None:
    assert seconds_to_ticks(Fraction(6, 5), MS) == 1200
    assert seconds_to_ticks(Fraction(0), MS) == 0
    assert seconds_to_ticks(Fraction(1), Fraction(1, 1_000_000)) == 1_000_000
    assert seconds_to_ticks(Fraction(3, 2), Fraction(1, 2)) == 3


@pytest.mark.parametrize("seconds", [Fraction(1, 3), Fraction("1.2345"), Fraction(1, 2000)])
def test_seconds_to_ticks_rejects_non_integral(seconds: Fraction) -> None:
    with pytest.raises(ValueError, match="whole number of ticks"):
        seconds_to_ticks(seconds, MS)


# -- ShotClock --------------------------------------------------------------------------


def _clock(
    frame_count: int = 105,
    fps: Fraction = Fraction(30),
    start_tick: int = 0,
    end_tick: int = 3500,
    tick_rate: Fraction = MS,
) -> ShotClock:
    return ShotClock(frame_count, fps, start_tick, end_tick, tick_rate)


def test_tick_at_exact_endpoints() -> None:
    clock = _clock()
    assert clock.tick_at(0) == 0
    # last frame shows the start of its interval, never end_tick itself
    assert clock.tick_at(104) == (3500 * 104) // 105 == 3466
    assert clock.tick_at(104) < clock.end_tick


def test_tick_at_real_time_maps_frames_to_ms() -> None:
    clock = _clock()
    assert [clock.tick_at(f) for f in (1, 30, 40, 60, 84, 85)] == [33, 1000, 1333, 2000, 2800, 2833]


def test_tick_at_with_offset_window() -> None:
    clock = _clock(frame_count=10, start_tick=1000, end_tick=2000)
    assert [clock.tick_at(f) for f in range(10)] == list(range(1000, 2000, 100))


def test_tick_at_rejects_frames_outside_shot() -> None:
    clock = _clock()
    for frame in (-1, 105, 1000):
        with pytest.raises(ValueError):
            clock.tick_at(frame)


def test_speed_and_durations() -> None:
    real_time = _clock()
    assert real_time.film_seconds == Fraction(7, 2)
    assert real_time.sim_seconds == Fraction(7, 2)
    assert real_time.speed == 1

    compressed = _clock(frame_count=240, end_tick=12_000)  # 12 s of sim in 8 s of film
    assert compressed.speed == Fraction(3, 2)

    slow = _clock(frame_count=90, end_tick=300, fps=Fraction(30000, 1001))
    assert slow.film_seconds == Fraction(90 * 1001, 30000)
    assert slow.speed == Fraction(300, 1000) / Fraction(90 * 1001, 30000)


@pytest.mark.parametrize(
    ("frame_count", "start_tick", "end_tick"),
    [(0, 0, 3500), (-5, 0, 3500), (105, 0, 0), (105, 10, 5)],
)
def test_shot_clock_rejects_invalid_inputs(
    frame_count: int, start_tick: int, end_tick: int
) -> None:
    with pytest.raises(ValueError):
        _clock(frame_count=frame_count, start_tick=start_tick, end_tick=end_tick)


@given(
    frame_count=st.integers(min_value=1, max_value=5000),
    start=st.integers(min_value=0, max_value=10**9),
    length=st.integers(min_value=1, max_value=10**9),
)
def test_tick_at_monotonic_and_within_window(frame_count: int, start: int, length: int) -> None:
    clock = _clock(frame_count=frame_count, start_tick=start, end_tick=start + length)
    ticks = [clock.tick_at(f) for f in range(frame_count)]
    assert ticks[0] == start
    assert all(start <= t < start + length for t in ticks)
    assert all(a <= b for a, b in itertools.pairwise(ticks))


@given(
    frame_count=st.integers(min_value=1, max_value=2000),
    length=st.integers(min_value=1, max_value=10**6),
)
def test_tick_at_strictly_increasing_when_window_covers_frames(
    frame_count: int, length: int
) -> None:
    if length < frame_count:
        length += frame_count
    clock = _clock(frame_count=frame_count, start_tick=0, end_tick=length)
    ticks = [clock.tick_at(f) for f in range(frame_count)]
    assert all(a < b for a, b in itertools.pairwise(ticks))
