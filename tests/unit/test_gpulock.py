from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from ostrium_kinetic.gpulock import gpu_lock, main


def test_lock_is_exclusive_across_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock = tmp_path / "gpu.lock"
    monkeypatch.setenv("KINETIC_GPU_LOCK", str(lock))
    holder = (
        "import time; from ostrium_kinetic.gpulock import gpu_lock\n"
        "with gpu_lock('holder'):\n"
        "    open(r'%s', 'w').close(); time.sleep(3)\n" % (tmp_path / "held")
    )
    proc = subprocess.Popen([sys.executable, "-c", holder])
    try:
        for _ in range(100):
            if (tmp_path / "held").exists():
                break
            time.sleep(0.05)
        assert (tmp_path / "held").exists()
        with pytest.raises(TimeoutError), gpu_lock("second", poll=0.1, timeout=0.5):
            pass
        started = time.monotonic()
        with gpu_lock("third", poll=0.1, timeout=10) as waited:
            assert waited > 0.5
        assert time.monotonic() - started < 10
    finally:
        proc.wait(timeout=10)


def test_lock_is_released_after_the_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KINETIC_GPU_LOCK", str(tmp_path / "gpu.lock"))
    with gpu_lock("a"):
        assert (tmp_path / "gpu.holder").read_text("utf-8").split()[1] == "a"
    assert not (tmp_path / "gpu.holder").exists()
    with gpu_lock("b", timeout=1):
        pass


def test_main_runs_the_command_under_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KINETIC_GPU_LOCK", str(tmp_path / "gpu.lock"))
    assert main(["--", sys.executable, "-c", "raise SystemExit(3)"]) == 3
    assert main([]) == 2
