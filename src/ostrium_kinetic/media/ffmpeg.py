"""Running ffmpeg/ffprobe, plus the small helpers every media step shares.

Argument builders are pure functions returning lists, so tests can assert command lines
without executing anything. Outputs are written to a temporary name next to the target and
moved into place with os.replace, so a crash never leaves a truncated file at the real path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from ostrium_kinetic.env.tools import discover, resolve
from ostrium_kinetic.workspace import Workspace


class MediaError(Exception):
    """An ffmpeg/ffprobe step failed or produced output that does not verify."""


@dataclass(frozen=True)
class Tools:
    ffmpeg: Path
    ffprobe: Path

    @classmethod
    def resolve(cls, ws: Workspace) -> Tools:
        """The ffmpeg and ffprobe Kinetic would run (kinetic.toml, KINETIC_*, PATH, known)."""
        found: dict[str, Path] = {}
        for name in ("ffmpeg", "ffprobe"):
            candidate = resolve(discover(name, ws, probe=False))
            if candidate is None:
                raise MediaError(
                    f"{name} not found: install FFmpeg or set [tools] {name} in kinetic.toml"
                )
            found[name] = Path(candidate.path)
        return cls(found["ffmpeg"], found["ffprobe"])


BASE_FLAGS: tuple[str, ...] = ("-hide_banner", "-nostdin", "-y")
# Deterministic container/stream headers (no muxer dates, no encoder version strings).
BITEXACT: tuple[str, ...] = ("-fflags", "+bitexact", "-flags:v", "+bitexact")


def rational(value: Fraction) -> str:
    """ffmpeg rational, always "num/den" ("30/1", "30000/1001")."""
    value = Fraction(value)
    return f"{value.numerator}/{value.denominator}"


def ffmpeg_command(tools: Tools, args: list[str]) -> list[str]:
    return [str(tools.ffmpeg), *BASE_FLAGS, *args]


def stderr_tail(text: str, lines: int = 8) -> str:
    kept = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _run(cmd: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            creationflags=flags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{Path(cmd[0]).name} timed out after {timeout:g} s") from exc
    except OSError as exc:
        raise MediaError(f"cannot run {cmd[0]}: {exc}") from exc


def run_ffmpeg(tools: Tools, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    """Run ffmpeg with -hide_banner -nostdin -y prepended; MediaError on non-zero exit."""
    proc = _run(ffmpeg_command(tools, args), timeout)
    if proc.returncode != 0:
        raise MediaError(f"ffmpeg exited {proc.returncode}:\n{stderr_tail(proc.stderr)}")
    return proc


def run_ffprobe(tools: Tools, args: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    proc = _run([str(tools.ffprobe), "-hide_banner", *args], timeout)
    if proc.returncode != 0:
        raise MediaError(f"ffprobe exited {proc.returncode}:\n{stderr_tail(proc.stderr)}")
    return proc


def tmp_path_for(out: Path) -> Path:
    """Temporary sibling of `out` that keeps its extension (ffmpeg picks the muxer by it)."""
    return out.with_name(f".{out.stem}.tmp-{os.getpid()}{out.suffix}")


@contextmanager
def atomic_output(out: Path) -> Iterator[Path]:
    """Yield a temporary path; on success move it to `out`, on failure delete it."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = tmp_path_for(out)
    tmp.unlink(missing_ok=True)
    try:
        yield tmp
        if not tmp.is_file():
            raise MediaError(f"ffmpeg wrote no output for {out.name}")
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
