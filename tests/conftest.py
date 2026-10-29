"""Shared fixtures for the unit suites."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FAILOVER_DIR = REPO_ROOT / "examples" / "smoke-failover"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def failover_module() -> Path:
    """The smoke-failover simulation module."""
    return FAILOVER_DIR / "sim" / "failover.py"


@pytest.fixture(scope="session")
def failover_params() -> dict[str, Any]:
    """The smoke-failover parameters the golden trace was produced with (seed 7)."""
    return {
        "latency_p_s": "1.5",
        "latency_s_s": "1.2",
        "ack_latency_s": "0.6",
        "cut_p_at_s": "1.2",
        "timeout_s": "1.5",
        "reroute_delay_s": "0.1",
        "jitter_ms": 20,
        "end_s": "3.5",
    }


@pytest.fixture(scope="session")
def golden_dir() -> Path:
    return REPO_ROOT / "tests" / "golden"
