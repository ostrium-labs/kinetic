"""A machine-wide lock so only one GPU render (Godot, Blender) runs at a time.

Several processes can share one laptop: a CLI render, a test run, a look-development script.
Two GPU renders at once on an integrated GPU make both slow and can starve the display, so
renderer adapters take this lock around the renderer process. It is an OS file lock: it is
released when the holder exits, even if it crashes.

Run any command under the lock with `python -m ostrium_kinetic.gpulock -- <command> ...`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def lock_path() -> Path:
    configured = os.environ.get("KINETIC_GPU_LOCK")
    return Path(configured) if configured else Path.home() / ".kinetic" / "gpu.lock"


def _try_lock(fd: int) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def gpu_lock(label: str, *, poll: float = 1.0, timeout: float | None = None) -> Iterator[float]:
    """Hold the lock for the duration of the block; yields the seconds spent waiting.
    Raises TimeoutError if `timeout` seconds pass without getting it."""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    started = time.monotonic()
    try:
        while not _try_lock(fd):
            if timeout is not None and time.monotonic() - started > timeout:
                raise TimeoutError(f"GPU lock {path} still held after {timeout:.0f} s")
            time.sleep(poll)
        waited = time.monotonic() - started
        holder = path.with_suffix(".holder")
        holder.write_text(f"{os.getpid()} {label}\n", encoding="utf-8")
        try:
            yield waited
        finally:
            holder.unlink(missing_ok=True)
            _unlock(fd)
    finally:
        os.close(fd)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--"]:
        args = args[1:]
    if not args:
        print("usage: python -m ostrium_kinetic.gpulock -- <command> [args...]", file=sys.stderr)
        return 2
    with gpu_lock(" ".join(args)[:200]) as waited:
        if waited > 1:
            print(f"gpulock: waited {waited:.0f} s for the GPU", file=sys.stderr)
        return subprocess.call(args)


if __name__ == "__main__":
    raise SystemExit(main())
