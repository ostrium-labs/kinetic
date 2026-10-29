"""Running a renderer process: output streamed to a log file, a hard timeout, and the whole
process tree killed when the timeout expires (renderers spawn helpers on Windows)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ProcessResult:
    returncode: int | None  # None when the process was killed on timeout
    seconds: float
    timed_out: bool
    log_path: Path

    def tail(self, lines: int = 20) -> str:
        return log_tail(self.log_path, lines)


def log_tail(path: Path, lines: int = 20) -> str:
    """The last `lines` non-empty lines of a log file ("" if it does not exist)."""
    try:
        text = path.read_text("utf-8", errors="replace")
    except OSError:
        return ""
    kept = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill a process and every descendant."""
    if proc.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            timeout=30,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)  # started with start_new_session=True
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def run_logged(
    args: Sequence[str],
    log_path: Path,
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> ProcessResult:
    """Run `args` (no shell), writing stdout and stderr to `log_path`. Never raises on a
    non-zero exit or a timeout; raises OSError if the executable cannot be started."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    with log_path.open("wb") as log:
        log.write(("$ " + subprocess.list2cmdline(list(args)) + "\n").encode("utf-8"))
        log.flush()
        windows = sys.platform == "win32"
        proc = subprocess.Popen(
            list(args),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if windows else 0,
            start_new_session=not windows,  # so kill_tree can signal the process group
        )
        try:
            returncode: int | None = proc.wait(timeout=timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            returncode = None
            timed_out = True
            log.write(f"\n[kinetic] killed after timeout of {timeout:g} s\n".encode())
    return ProcessResult(returncode, time.perf_counter() - start, timed_out, log_path)
