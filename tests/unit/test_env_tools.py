"""Tool discovery, version parsing, compatibility ranges and shadowing."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from ostrium_kinetic.env.tools import (
    Candidate,
    RunResult,
    discover,
    find_shadowing,
    parse_version,
    path_dirs_from_env,
    probe_version,
    resolve,
    toml_line,
    version_ok,
    version_tuple,
)

WIN = sys.platform == "win32"
EXT = ".exe" if WIN else ""
ENV = {"PATHEXT": ".COM;.EXE;.BAT;.CMD"} if WIN else {}

# Real version output measured on the M0 machine (m0-report.md).
REAL = {
    "godot": ("4.7.2.stable.official.ed1daf0bf", "4.7.2"),
    "blender": ("Blender 5.2.3 LTS\n\tbuild date: 2026-10-22\n", "5.2.3"),
    "ffmpeg": (
        "ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright (c) 2000-2026 "
        "the FFmpeg developers",
        "9.0.2",
    ),
    "ffprobe": (
        "ffprobe version 9.0.2-full_build-www.gyan.dev Copyright (c) 2007-2026 "
        "the FFmpeg developers",
        "9.0.2",
    ),
    "manim": ("Manim Community v0.22.1\n", "0.22.1"),
    "uv": ("uv 0.11.31 (b7fdec626 2026-07-21 x86_64-pc-windows-msvc)", "0.11.31"),
    "python": ("Python 3.12.9", "3.12.9"),
    "melt": ("melt.exe 7.43.0\nCopyright (C) 2002-2026 Meltytech, LLC", "7.43.0"),
    "kdenlive": ("kdenlive 26.08.2", "26.08.2"),
}


@pytest.mark.parametrize("tool", sorted(REAL))
def test_parse_real_version_strings(tool: str) -> None:
    text, want = REAL[tool]
    assert parse_version(tool, text) == want


def test_parse_version_variants() -> None:
    assert (
        parse_version("uv", "uv 0.13.6 (329bf9448 2026-10-26 x86_64-pc-windows-msvc)") == "0.13.6"
    )
    assert parse_version("ffmpeg", "ffmpeg version n7.1 Copyright") == "7.1"
    assert parse_version("ffmpeg", "ffmpeg version N-117000-gabc Copyright") is None
    assert parse_version("melt", "melt 7.22.0") == "7.22.0"
    assert parse_version("godot", "garbage") is None
    assert parse_version("latex", "pdfTeX 3.141592653-2.6-1.40.25 (TeX Live 2023)") == "3.141592653"


def test_version_ranges() -> None:
    assert version_tuple("4.7.2") == (4, 7, 2)
    assert version_ok("3.12.13", ("==", "3.12"))
    assert not version_ok("3.13.0", ("==", "3.12"))
    assert version_ok("0.13.6", (">=", "0.11"))
    assert not version_ok("0.10.9", (">=", "0.11"))
    assert version_ok("9.0.2", (">=", "7"))
    assert not version_ok("6.1", (">=", "7"))
    assert version_ok("4.7.2", ("==", "4.7"))
    assert not version_ok("4.8.0", ("==", "4.7"))


def test_probe_version_uses_injected_runner() -> None:
    seen: list[list[str]] = []

    def runner(args: Sequence[str], timeout: float) -> RunResult:
        seen.append(list(args))
        return RunResult(0, "ffmpeg version 9.0.2-full_build-www.gyan.dev Copyright\n", "")

    version, banner, error = probe_version("ffmpeg", "ffmpeg.exe", runner)
    assert (version, error) == ("9.0.2", None)
    assert banner is not None and banner.startswith("ffmpeg version")
    assert seen == [["ffmpeg.exe", "-version"]]

    def broken(args: Sequence[str], timeout: float) -> RunResult:
        return RunResult(9, "", "Python was not found")

    version, _, error = probe_version("python", "python.exe", broken)
    assert version is None and error is not None and "exit 9" in error


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    if not WIN:
        path.chmod(0o755)
    return path


def _same(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def test_discovery_order_and_all_path_hits(tmp_path: Path) -> None:
    pinned = _touch(tmp_path / "pinned" / f"uv{EXT}")
    envd = _touch(tmp_path / "envd" / f"uv{EXT}")
    a = _touch(tmp_path / "a" / f"uv{EXT}")
    b = _touch(tmp_path / "b" / f"uv{EXT}")
    known = _touch(tmp_path / "winget" / "astral-sh.uv_x" / f"uv{EXT}")
    _touch(tmp_path / "a" / f"uvx{EXT}")  # must not match
    env = {**ENV, "KINETIC_UV": str(envd)}
    found = discover(
        "uv",
        env=env,
        config={"uv": str(pinned)},
        path_dirs=[str(tmp_path / "a"), str(tmp_path / "missing"), str(tmp_path / "b")],
        known_globs=[str(tmp_path / "winget" / "astral-sh.uv_*" / f"uv{EXT}")],
        probe=False,
    )
    assert [c.source for c in found] == ["env", "config", "path", "path", "known"]
    for cand, want in zip(found, [envd, pinned, a, b, known], strict=True):
        assert _same(cand.path, want)
    first = resolve(found)
    assert first is not None and first.source == "env"


def test_discovery_dedupes_by_resolved_path(tmp_path: Path) -> None:
    exe = _touch(tmp_path / "bin" / f"ffmpeg{EXT}")
    found = discover(
        "ffmpeg",
        env=dict(ENV),
        config={"ffmpeg": str(exe)},
        path_dirs=[str(tmp_path / "bin"), str(tmp_path / "bin")],
        known_globs=[str(tmp_path / "bin" / f"ffmpeg{EXT}")],
        probe=False,
    )
    assert len(found) == 1 and found[0].source == "config"


def test_missing_pin_is_reported_and_skipped(tmp_path: Path) -> None:
    real = _touch(tmp_path / "bin" / f"blender{EXT}")
    found = discover(
        "blender",
        env=dict(ENV),
        config={"blender": str(tmp_path / "nope" / f"blender{EXT}")},
        path_dirs=[],
        known_globs=[str(real)],
        probe=False,
    )
    assert found[0].source == "config" and found[0].error == "file not found"
    chosen = resolve(found)
    assert chosen is not None and chosen.source == "known"


@pytest.mark.skipif(not WIN, reason="PATHEXT and _console naming are Windows-specific")
def test_windows_pathext_and_godot_console(tmp_path: Path) -> None:
    d = tmp_path / "godot"
    console = _touch(d / "Godot_v4.7.2-stable_win64_console.exe")
    _touch(d / "Godot_v4.7.2-stable_win64.exe")  # GUI build: not a match
    cmd = _touch(tmp_path / "shim" / "godot.CMD")
    _touch(tmp_path / "shim" / "godot.txt")  # not in PATHEXT
    found = discover(
        "godot",
        env=dict(ENV),
        config={},
        path_dirs=[str(tmp_path / "shim"), str(d)],
        known_globs=[],
        probe=False,
    )
    assert len(found) == 2
    assert _same(found[0].path, cmd)
    assert _same(found[1].path, console)


def test_discovery_probes_versions_via_runner(tmp_path: Path) -> None:
    exe = _touch(tmp_path / "bin" / f"manim{EXT}")

    def runner(args: Sequence[str], timeout: float) -> RunResult:
        assert list(args)[1:] == ["--version"]
        return RunResult(0, "Manim Community v0.22.1\n", "")

    found = discover(
        "manim",
        env=dict(ENV),
        config={},
        path_dirs=[str(exe.parent)],
        known_globs=[],
        runner=runner,
    )
    assert [c.version for c in found] == ["0.22.1"]


def test_path_dirs_from_env() -> None:
    if WIN:
        env = {"Path": r'C:\a;;"C:\b c";C:\d'}
        assert path_dirs_from_env(env, "win32") == [r"C:\a", r"C:\b c", r"C:\d"]
    assert path_dirs_from_env({"PATH": "/usr/bin::/bin"}, "linux") == ["/usr/bin", "/bin"]


def _c(path: str, source: str, version: str | None) -> Candidate:
    return Candidate(path, source, version)  # type: ignore[arg-type]


def test_shadowing_detects_older_first_path_hit() -> None:
    # The measured M0 case: pip uv 0.11.31 before winget uv 0.13.6.
    old = _c(r"C:\Python312\Scripts\uv.exe", "path", "0.11.31")
    new = _c(r"C:\WinGet\Packages\astral-sh.uv_x\uv.exe", "path", "0.13.6")
    shadow = find_shadowing([old, new])
    assert shadow is not None
    assert shadow.first_path is old and shadow.best is new


def test_shadowing_none_when_first_is_newest_or_versions_equal() -> None:
    new = _c("a/uv", "path", "0.13.6")
    old = _c("b/uv", "path", "0.11.31")
    assert find_shadowing([new, old]) is None
    assert find_shadowing([_c("a/uv", "path", "1.0"), _c("b/uv", "path", "1.0")]) is None
    assert find_shadowing([_c("a/uv", "path", "1.0")]) is None


def test_shadowing_counts_known_and_ignores_unversioned() -> None:
    first = _c("a/ffmpeg", "path", "7.1")
    known = _c("w/ffmpeg", "known", "9.0.2")
    shadow = find_shadowing([first, _c("x/ffmpeg", "path", None), known])
    assert shadow is not None and shadow.best is known
    assert find_shadowing([_c("a", "path", None), _c("b", "path", "9.0")]) is None


@pytest.mark.skipif(not WIN, reason="Windows path syntax")
def test_toml_line_uses_forward_slashes() -> None:
    line = toml_line("blender", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
    assert line == 'blender = "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"'
