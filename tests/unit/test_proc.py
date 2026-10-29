"""Running external tools: output capture and a timeout that kills the whole process tree."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ostrium_kinetic import proc
from ostrium_kinetic.env.tools import run as tools_run

_CHILD = """\
import os, sys, time
with open(sys.argv[1], "w") as fh:
    fh.write(str(os.getpid()))
print("child started", flush=True)
time.sleep(60)
"""


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return f'"{pid}"' in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A killed child of a session leader may linger as a zombie until reaped by init.
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            return fh.read().split()[2] != "Z"
    except OSError:
        return True


def _wrapper(tmp_path: Path) -> tuple[list[str], Path]:
    """A .cmd (Windows) or .sh (POSIX) wrapper whose child sleeps for a minute while holding
    the inherited stdout/stderr, like a tool launched through a shell script."""
    child = tmp_path / "child.py"
    child.write_text(_CHILD, encoding="utf-8")
    pid_file = tmp_path / "child.pid"
    if sys.platform == "win32":
        script = tmp_path / "slowtool.cmd"
        script.write_text(
            f'@echo off\r\necho wrapper started\r\n"{sys.executable}" "{child}" "{pid_file}"\r\n',
            encoding="utf-8",
        )
        return [str(script)], pid_file
    script = tmp_path / "slowtool.sh"
    script.write_text(
        f'#!/bin/sh\necho wrapper started\n"{sys.executable}" "{child}" "{pid_file}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    return [str(script)], pid_file


def _wait_for(path: Path, seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_captures_output_and_exit_code() -> None:
    code = "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"
    done = proc.run([sys.executable, "-c", code], timeout=30)
    assert (done.returncode, done.stdout, done.stderr, done.timed_out) == (
        3,
        "out\n",
        "err\n",
        False,
    )
    assert done.seconds >= 0


def test_passes_cwd_and_env(tmp_path: Path) -> None:
    code = "import os; print(os.getcwd()); print(os.environ['KINETIC_PROC_TEST'])"
    env = {**os.environ, "KINETIC_PROC_TEST": "yes"}
    done = proc.run([sys.executable, "-c", code], timeout=30, cwd=tmp_path, env=env)
    cwd, value = done.stdout.splitlines()
    assert Path(cwd).resolve() == tmp_path.resolve()
    assert value == "yes"


def test_missing_executable_raises_oserror(tmp_path: Path) -> None:
    with pytest.raises(OSError):
        proc.run([str(tmp_path / "no-such-tool.exe")], timeout=5)
    assert tools_run([str(tmp_path / "no-such-tool.exe")], 5).returncode == -1


def test_timeout_kills_grandchildren_and_returns_promptly(tmp_path: Path) -> None:
    args, pid_file = _wrapper(tmp_path)
    started = time.monotonic()
    done = proc.run(args, timeout=2)
    elapsed = time.monotonic() - started
    assert done.timed_out
    assert done.returncode == -1
    assert elapsed < 6, f"took {elapsed:.1f} s"
    assert "wrapper started" in done.stdout
    _wait_for(pid_file, 5)  # the child normally wrote it long before the timeout
    assert pid_file.exists(), "the child never started"
    pid = int(pid_file.read_text("utf-8"))
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid), f"child {pid} survived the timeout"


def test_tools_run_reports_timeout(tmp_path: Path) -> None:
    args, _ = _wrapper(tmp_path)
    started = time.monotonic()
    result = tools_run(args, 2)
    assert time.monotonic() - started < 6
    assert result.timed_out
    assert result.stderr.endswith("timed out after 2 s")
