"""Running an external tool with a timeout that holds, and killing its whole process tree.

`subprocess.run(..., capture_output=True, timeout=...)` is not enough on Windows: after the
timeout it kills only the direct child, then waits for the output pipes to close, which
never happens while a grandchild (a tool started by a .cmd wrapper, a renderer's helper
process) still holds them. Here output goes to temporary files instead of pipes, so waiting
depends only on the direct child, and on timeout the whole tree is killed:
`taskkill /F /T` on Windows, the child's own session (process group) on POSIX.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

_KILL_WAIT_S = 10.0


@dataclass(frozen=True)
class CompletedRun:
    returncode: int  # -1 when the process was killed after a timeout
    stdout: str
    stderr: str
    timed_out: bool
    seconds: float


def kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Kill `proc` and every process it started. Never raises."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=_KILL_WAIT_S,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                check=False,
            )
    else:
        with contextlib.suppress(OSError):
            os.killpg(proc.pid, signal.SIGKILL)  # the child leads its own session
    with contextlib.suppress(OSError):
        proc.kill()  # in case taskkill was unavailable or the group was already gone


def _read(fh: IO[bytes]) -> str:
    fh.seek(0)
    text = fh.read().decode("utf-8", "replace")
    return text.replace("\r\n", "\n")


def run(
    args: Sequence[str],
    timeout: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> CompletedRun:
    """Run a command (list args, no shell, no stdin) and capture its text output.

    Returns within about `timeout` seconds (plus the time to kill the tree) however many
    descendants the command started. Raises OSError when the command cannot be started.
    """
    start = time.monotonic()
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        if sys.platform == "win32":
            proc = subprocess.Popen(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=None if env is None else dict(env),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            proc = subprocess.Popen(
                list(args),
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                cwd=cwd,
                env=None if env is None else dict(env),
                start_new_session=True,
            )
        timed_out = False
        try:
            returncode = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            kill_tree(proc)
            with contextlib.suppress(subprocess.TimeoutExpired):
                proc.wait(timeout=_KILL_WAIT_S)
            returncode = -1
        except BaseException:  # Ctrl-C while waiting: do not leave the tree running
            kill_tree(proc)
            raise
        return CompletedRun(returncode, _read(out), _read(err), timed_out, time.monotonic() - start)
