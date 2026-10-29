from __future__ import annotations

import pytest

from ostrium_kinetic.sim.rng import RngStreams


def _draws(rng: RngStreams, name: str, n: int = 20) -> list[int]:
    return [rng.int_between(name, 0, 1_000_000) for _ in range(n)]


def test_same_seed_and_name_give_same_sequence() -> None:
    assert _draws(RngStreams(42), "jitter") == _draws(RngStreams(42), "jitter")


def test_different_seeds_differ() -> None:
    assert _draws(RngStreams(1), "jitter") != _draws(RngStreams(2), "jitter")


def test_different_names_are_independent_streams() -> None:
    rng = RngStreams(42)
    assert _draws(rng, "a") != _draws(rng, "b")


def test_adding_a_stream_does_not_shift_another() -> None:
    alone = RngStreams(9)
    expected = _draws(alone, "link.P", 10)

    mixed = RngStreams(9)
    got: list[int] = []
    for _ in range(10):
        mixed.int_between("new.stream", -50, 50)  # interleaved draws on another stream
        got.append(mixed.int_between("link.P", 0, 1_000_000))
    assert got == expected


def test_stream_is_reused_not_reseeded() -> None:
    rng = RngStreams(3)
    assert rng.stream("x") is rng.stream("x")
    first, second = rng.int_between("x", 0, 10**9), rng.int_between("x", 0, 10**9)
    assert first != second


def test_int_between_is_inclusive() -> None:
    rng = RngStreams(5)
    values = {rng.int_between("coin", 0, 1) for _ in range(200)}
    assert values == {0, 1}
    assert {rng.int_between("fixed", 4, 4) for _ in range(5)} == {4}
    assert all(-3 <= rng.int_between("range", -3, 3) <= 3 for _ in range(200))


def test_int_between_returns_python_int() -> None:
    value = RngStreams(0).int_between("t", 0, 10)
    assert type(value) is int


def test_negative_seed_rejected() -> None:
    with pytest.raises(ValueError):
        RngStreams(-1)


# Pinned values: these guard cross-platform determinism. If they change, every committed
# trace changes too; that is a contract break, not a test to update casually.
def test_pinned_known_values() -> None:
    rng = RngStreams(7)
    assert [rng.int_between("a", 0, 1000) for _ in range(5)] == [185, 11, 183, 91, 400]
    assert [rng.int_between("b", -20, 20) for _ in range(5)] == [-12, -10, 17, -3, -1]

    big = RngStreams(12345)
    assert [big.int_between("jitter", 0, 10**9) for _ in range(3)] == [
        645998132,
        313042070,
        142434334,
    ]


def test_pinned_failover_jitter() -> None:
    # smoke-failover, seed 7, jitter 20 ms: link P = 1500 - 12, link S = 1200 + 12
    rng = RngStreams(7)
    assert rng.int_between("jitter.P", -20, 20) == -12
    assert rng.int_between("jitter.S", -20, 20) == 12
