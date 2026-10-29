"""Named, independent random streams derived from one seed.

Each stream is a numpy PCG64 Generator seeded from SeedSequence(seed, spawn_key=hash(name)),
so adding a new stream never shifts the numbers an existing stream produces.
"""

from __future__ import annotations

import hashlib

import numpy as np


def _name_key(name: str) -> int:
    return int.from_bytes(hashlib.sha256(name.encode("utf-8")).digest()[:8], "little")


class RngStreams:
    def __init__(self, seed: int) -> None:
        if seed < 0:
            raise ValueError("seed must be >= 0")
        self.seed = seed
        self._streams: dict[str, np.random.Generator] = {}

    def stream(self, name: str) -> np.random.Generator:
        if name not in self._streams:
            seq = np.random.SeedSequence(entropy=self.seed, spawn_key=(_name_key(name),))
            self._streams[name] = np.random.Generator(np.random.PCG64(seq))
        return self._streams[name]

    def int_between(self, name: str, low: int, high: int) -> int:
        """Uniform integer in [low, high], inclusive. Prefer this for timing jitter: integer
        draws are bit-identical across platforms, unlike floating-point distributions."""
        return int(self.stream(name).integers(low, high, endpoint=True))
