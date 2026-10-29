"""Find external tools: kinetic.toml -> KINETIC_<TOOL> -> every PATH hit -> known locations.

Discovery reports *all* candidates (not just the first) so the doctor can explain shadowing.
Everything that touches the machine is injectable (env, PATH dirs, known globs, the process
runner), so tests can build fake tool directories without executing anything.
"""

from __future__ import annotations

import fnmatch
import glob
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from ostrium_kinetic.workspace import Workspace

Source = Literal["config", "env", "path", "known"]

TOOLS: tuple[str, ...] = (
    "python",
    "uv",
    "ffmpeg",
    "ffprobe",
    "godot",
    "blender",
    "manim",
    "melt",
    "kdenlive",
    "latex",
    "dvisvgm",
    "typst",
)


@dataclass(frozen=True)
class RunResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def output(self) -> str:
        return (self.stdout or "") + (self.stderr or "")

    def tail(self, lines: int = 3, width: int = 200) -> str:
        """Last non-empty output lines, for short error reports."""
        text = [ln.strip() for ln in self.output.splitlines() if ln.strip()]
        out = " | ".join(text[-lines:])
        return out[-width:] if len(out) > width else out


Runner = Callable[[Sequence[str], float], RunResult]


def run(args: Sequence[str], timeout: float, cwd: Path | None = None) -> RunResult:
    """Run a command (list args, no shell) and capture text output; never raises."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            stdin=subprocess.DEVNULL,
            cwd=cwd,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else ""
        err = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else ""
        return RunResult(-1, out, err + f"\ntimed out after {timeout:g} s", timed_out=True)
    except OSError as exc:
        return RunResult(-1, "", f"{type(exc).__name__}: {exc}")
    return RunResult(proc.returncode, proc.stdout or "", proc.stderr or "")


@dataclass(frozen=True)
class Candidate:
    """One place a tool was found. `version` is the parsed version ("4.7.2"), `banner` the
    first line of the version output, `error` why it is unusable or unprobed."""

    path: str
    source: Source
    version: str | None = None
    error: str | None = None
    banner: str | None = None

    @property
    def exists(self) -> bool:
        return Path(self.path).is_file()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# ---------------------------------------------------------------- version parsing


_VERSION_PATTERNS: dict[str, str] = {
    "godot": r"^\s*(\d+\.\d+(?:\.\d+)?)\.[A-Za-z]",
    "blender": r"Blender\s+(\d+\.\d+(?:\.\d+)?)",
    "ffmpeg": r"ffmpeg version\s+n?(\d+(?:\.\d+)+|\d+)(?=[\s\-_+~]|$)",
    "ffprobe": r"ffprobe version\s+n?(\d+(?:\.\d+)+|\d+)(?=[\s\-_+~]|$)",
    "manim": r"Manim Community v(\d+\.\d+\.\d+)",
    "uv": r"\buv\s+(\d+\.\d+\.\d+)",
    "python": r"Python\s+(\d+\.\d+\.\d+)",
    "melt": r"melt(?:\.exe)?\s+(\d+\.\d+\.\d+)",
    "kdenlive": r"kdenlive\s+(\d+\.\d+\.\d+)",
    "dvisvgm": r"dvisvgm\s+(\d+\.\d+(?:\.\d+)?)",
    "typst": r"typst\s+(\d+\.\d+\.\d+)",
}
_GENERIC = r"(\d+\.\d+(?:\.\d+)?)"


def parse_version(tool: str, text: str) -> str | None:
    """Extract a dotted version from a tool's version output; None when unrecognised."""
    pattern = _VERSION_PATTERNS.get(tool, _GENERIC)
    match = re.search(pattern, text, flags=re.MULTILINE)
    return match.group(1) if match else None


def version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version))


# (operator, bound): "==" means same leading components ("3.12" matches 3.12.x).
COMPAT: dict[str, tuple[str, str]] = {
    "python": ("==", "3.12"),
    "uv": (">=", "0.11"),
    "godot": ("==", "4.7"),
    "blender": ("==", "5.2"),
    "ffmpeg": (">=", "7"),
    "ffprobe": (">=", "7"),
    "manim": ("==", "0.22"),
}


def describe_range(spec: tuple[str, str]) -> str:
    op, bound = spec
    return f"{bound}.x" if op == "==" else f"{op} {bound}"


def version_ok(version: str, spec: tuple[str, str]) -> bool:
    op, bound = spec
    have, want = version_tuple(version), version_tuple(bound)
    if op == "==":
        return have[: len(want)] == want
    if op == ">=":
        return have >= want
    raise ValueError(f"unknown operator {op!r}")


_VERSION_ARGS: dict[str, tuple[str, ...]] = {"ffmpeg": ("-version",), "ffprobe": ("-version",)}
_VERSION_TIMEOUT: dict[str, float] = {"blender": 90.0, "godot": 60.0, "kdenlive": 20.0}


def probe_version(
    tool: str, path: str, runner: Runner = run
) -> tuple[str | None, str | None, str | None]:
    """Run the tool's version command. Returns (version, banner, error)."""
    args = [path, *_VERSION_ARGS.get(tool, ("--version",))]
    result = runner(args, _VERSION_TIMEOUT.get(tool, 30.0))
    lines = [ln.strip() for ln in result.output.splitlines() if ln.strip()]
    banner = lines[0] if lines else None
    version = parse_version(tool, result.stdout) or parse_version(tool, result.stderr)
    if version:
        return version, banner, None
    if result.timed_out:
        return None, banner, "version probe timed out"
    return None, banner, f"could not read version (exit {result.returncode}): {result.tail(2)}"


# ---------------------------------------------------------------- discovery


def _is_windows(platform: str) -> bool:
    return platform == "win32"


def _env_get(env: Mapping[str, str], key: str, platform: str) -> str | None:
    if key in env:
        return env[key]
    if _is_windows(platform):
        lowered = key.lower()
        for k, v in env.items():
            if k.lower() == lowered:
                return v
    return None


def exe_patterns(tool: str, platform: str = sys.platform) -> list[str]:
    """Executable base names (fnmatch patterns, without extension on Windows)."""
    if tool == "godot":
        # `_console` first: the GUI-subsystem build prints nothing to a pipe on Windows.
        return (
            ["godot", "godot4", "Godot_v*_console"]
            if _is_windows(platform)
            else [
                "godot",
                "godot4",
                "Godot_v*",
            ]
        )
    if tool == "python":
        return ["python", "python3"]
    return [tool]


def path_dirs_from_env(env: Mapping[str, str], platform: str = sys.platform) -> list[str]:
    raw = _env_get(env, "PATH", platform) or ""
    sep = ";" if _is_windows(platform) else ":"
    return [d.strip().strip('"') for d in raw.split(sep) if d.strip().strip('"')]


def pathext(env: Mapping[str, str], platform: str = sys.platform) -> list[str]:
    if not _is_windows(platform):
        return [""]
    raw = _env_get(env, "PATHEXT", platform) or ".COM;.EXE;.BAT;.CMD"
    return [e.lower() for e in raw.split(";") if e]


def _match_dir(
    directory: str, patterns: Sequence[str], exts: Sequence[str], platform: str
) -> list[str]:
    """All files in `directory` matching any pattern, in pattern order then extension order."""
    try:
        entries = sorted(os.listdir(directory), reverse=True)
    except OSError:
        return []
    hits: list[str] = []
    for pattern in patterns:
        for ext in exts:
            for name in entries:
                if _is_windows(platform):
                    stem, dot, suffix = name.rpartition(".")
                    if not dot or f".{suffix.lower()}" != ext:
                        continue
                    if not fnmatch.fnmatch(stem.lower(), pattern.lower()):
                        continue
                elif not fnmatch.fnmatchcase(name, pattern):
                    continue
                full = os.path.join(directory, name)
                if full in hits or not os.path.isfile(full):
                    continue
                if not _is_windows(platform) and not os.access(full, os.X_OK):
                    continue
                hits.append(full)
    return hits


def path_hits(
    tool: str,
    path_dirs: Iterable[str],
    exts: Sequence[str],
    platform: str = sys.platform,
) -> list[str]:
    """Every PATH hit for the tool, in PATH order."""
    patterns = exe_patterns(tool, platform)
    hits: list[str] = []
    for directory in path_dirs:
        hits.extend(_match_dir(directory, patterns, exts, platform))
    return hits


def default_known_globs(
    tool: str, env: Mapping[str, str], platform: str = sys.platform
) -> list[str]:
    """Standard install locations for a tool as glob patterns."""
    if _is_windows(platform):
        local = _env_get(env, "LOCALAPPDATA", platform) or ""
        home = _env_get(env, "USERPROFILE", platform) or ""
        pf = _env_get(env, "ProgramFiles", platform) or r"C:\Program Files"
        winget = os.path.join(local, "Microsoft", "WinGet", "Packages") if local else ""
        links = os.path.join(local, "Microsoft", "WinGet", "Links") if local else ""
        table: dict[str, list[str]] = {
            "godot": [
                os.path.join(winget, "GodotEngine.GodotEngine_*", "Godot_v*_win64_console.exe")
            ],
            "blender": [os.path.join(pf, "Blender Foundation", "Blender *", "blender.exe")],
            "kdenlive": [
                os.path.join(local, "Programs", "Kdenlive", "bin", "kdenlive.exe"),
                os.path.join(pf, "kdenlive", "bin", "kdenlive.exe"),
            ],
            "melt": [
                os.path.join(local, "Programs", "Kdenlive", "bin", "melt.exe"),
                os.path.join(pf, "kdenlive", "bin", "melt.exe"),
            ],
            "uv": [
                os.path.join(winget, "astral-sh.uv_*", "uv.exe"),
                os.path.join(home, ".local", "bin", "uv.exe"),
                os.path.join(home, ".cargo", "bin", "uv.exe"),
            ],
            "manim": [os.path.join(home, ".local", "bin", "manim.exe")],
            "ffmpeg": [
                os.path.join(winget, "Gyan.FFmpeg_*", "ffmpeg-*", "bin", "ffmpeg.exe"),
                os.path.join(links, "ffmpeg.exe"),
            ],
            "ffprobe": [
                os.path.join(winget, "Gyan.FFmpeg_*", "ffmpeg-*", "bin", "ffprobe.exe"),
                os.path.join(links, "ffprobe.exe"),
            ],
            "python": [os.path.join(local, "Programs", "Python", "Python312", "python.exe")],
            "typst": [os.path.join(winget, "Typst.Typst_*", "typst-*", "typst.exe")],
            "latex": [
                os.path.join(local, "Programs", "MiKTeX", "miktex", "bin", "x64", "latex.exe"),
                os.path.join(pf, "MiKTeX", "miktex", "bin", "x64", "latex.exe"),
            ],
            "dvisvgm": [
                os.path.join(local, "Programs", "MiKTeX", "miktex", "bin", "x64", "dvisvgm.exe"),
                os.path.join(pf, "MiKTeX", "miktex", "bin", "x64", "dvisvgm.exe"),
            ],
        }
        # Drop patterns built from a missing env var (they would be relative to the cwd).
        return [g for g in table.get(tool, []) if os.path.isabs(g)]
    home = _env_get(env, "HOME", platform) or os.path.expanduser("~")
    dirs = ["/usr/bin", "/usr/local/bin", "/snap/bin", os.path.join(home, ".local", "bin")]
    return [os.path.join(d, p) for d in dirs for p in exe_patterns(tool, platform)]


def _canonical(path: str) -> str:
    try:
        resolved = os.path.realpath(path)
    except (OSError, ValueError):
        resolved = os.path.abspath(path)
    return os.path.normcase(resolved)


def discover(
    tool: str,
    ws: Workspace | None = None,
    *,
    env: Mapping[str, str] | None = None,
    path_dirs: Sequence[str] | None = None,
    known_globs: Sequence[str] | None = None,
    config: Mapping[str, str] | None = None,
    platform: str = sys.platform,
    probe: bool = True,
    runner: Runner = run,
) -> list[Candidate]:
    """All candidates for `tool` in resolution order, deduplicated by resolved path."""
    env = os.environ if env is None else env
    if config is None:
        config = ws.tools if ws is not None else {}
    raw: list[tuple[str, Source]] = []

    # A KINETIC_<TOOL> environment variable overrides kinetic.toml, which overrides PATH.
    env_value = _env_get(env, f"KINETIC_{tool.upper()}", platform)
    if env_value:
        raw.append((str(Path(env_value).expanduser()), "env"))
    configured = config.get(tool)
    if configured:
        p = Path(configured).expanduser()
        if not p.is_absolute() and ws is not None:
            p = ws.root / p
        raw.append((str(p), "config"))
    dirs = path_dirs if path_dirs is not None else path_dirs_from_env(env, platform)
    raw.extend((hit, "path") for hit in path_hits(tool, dirs, pathext(env, platform), platform))
    globs = known_globs if known_globs is not None else default_known_globs(tool, env, platform)
    for pattern in globs:
        for hit in sorted(glob.glob(pattern), reverse=True):
            if os.path.isfile(hit):
                raw.append((hit, "known"))

    seen: set[str] = set()
    out: list[Candidate] = []
    for path, source in raw:
        key = _canonical(path)
        if key in seen:
            continue
        seen.add(key)
        if not os.path.isfile(path):
            out.append(Candidate(path, source, error="file not found"))
        elif probe:
            version, banner, error = probe_version(tool, path, runner)
            out.append(Candidate(path, source, version, error, banner))
        else:
            out.append(Candidate(path, source))
    return out


def discover_all(
    ws: Workspace | None,
    tools: Sequence[str] = TOOLS,
    *,
    env: Mapping[str, str] | None = None,
    probe: bool = True,
    runner: Runner = run,
) -> dict[str, list[Candidate]]:
    """Discover several tools; version probes run in parallel."""
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {
            t: pool.submit(discover, t, ws, env=env, probe=probe, runner=runner) for t in tools
        }
        return {t: f.result() for t, f in futures.items()}


def resolve(candidates: Sequence[Candidate]) -> Candidate | None:
    """The candidate Kinetic would run: the first one that exists."""
    return next((c for c in candidates if c.exists), None)


@dataclass(frozen=True)
class Shadowing:
    first_path: Candidate
    best: Candidate
    others: list[Candidate] = field(default_factory=list)


def find_shadowing(candidates: Sequence[Candidate]) -> Shadowing | None:
    """When the first PATH hit is older than another candidate's version, say which."""
    versioned = [c for c in candidates if c.version]
    if len({c.version for c in versioned}) < 2:
        return None
    first_path = next((c for c in candidates if c.source == "path"), None)
    if first_path is None or not first_path.version:
        return None
    best = max(versioned, key=lambda c: version_tuple(c.version or ""))
    if version_tuple(best.version or "") <= version_tuple(first_path.version):
        return None
    return Shadowing(first_path, best, [c for c in versioned if c not in (first_path, best)])


def toml_line(tool: str, path: str) -> str:
    """The kinetic.toml [tools] entry that pins a tool to `path`."""
    return f'{tool} = "{Path(path).as_posix()}"'
