"""Individual doctor checks. Each builder is pure over already-gathered facts."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from ostrium_kinetic.env.hardware import Gpu, disk_status
from ostrium_kinetic.env.probes import WANTED_ENCODERS, ProbeResult
from ostrium_kinetic.env.tools import (
    COMPAT,
    Candidate,
    describe_range,
    find_shadowing,
    resolve,
    toml_line,
    version_ok,
)

Status = Literal["PASS", "WARN", "FAIL", "SKIP", "INFO"]
STATUSES: tuple[Status, ...] = ("FAIL", "WARN", "PASS", "INFO", "SKIP")

REQUIRED = ("python", "uv", "ffmpeg", "ffprobe")
LATER = {"godot": "M2", "blender": "M4"}
OPTIONAL = ("manim", "melt", "kdenlive", "typst")


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    status: Status
    detail: str
    remedy: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "remedy": self.remedy,
            "data": self.data,
        }


def _cand_data(candidates: Sequence[Candidate]) -> dict[str, Any]:
    return {"candidates": [c.to_dict() for c in candidates]}


def _label(c: Candidate) -> str:
    return f"{c.version or '?'} ({c.path})"


# ---------------------------------------------------------------- 1-3: tools


def check_resolution(tool: str, candidates: Sequence[Candidate]) -> Check:
    """Check 1: is the tool found, and how (pinned, PATH, or only a known location)."""
    cid, title = f"tool.{tool}", f"{tool} executable"
    data = _cand_data(candidates)
    missing = [c for c in candidates if c.source in ("config", "env") and not c.exists]
    note = "".join(f"; {c.source} entry points to a missing file: {c.path}" for c in missing)
    chosen = resolve(candidates)
    if chosen is None:
        if tool in REQUIRED:
            return Check(
                cid, title, "FAIL", f"not found{note}", f"install {tool} (needed for M1)", data
            )
        if tool in LATER:
            return Check(
                cid,
                title,
                "WARN",
                f"not found (needed from {LATER[tool]}){note}",
                f"install {tool}, then pin it in kinetic.toml [tools] if it is not on PATH",
                data,
            )
        return Check(cid, title, "SKIP", f"not found (optional){note}", None, data)
    data["resolved"] = chosen.path
    found = f"{_label(chosen)} via {chosen.source}"
    others = len(candidates) - 1
    if others > 0:
        found += f"; {others} other candidate(s)"
    if chosen.source == "known":
        return Check(
            cid,
            title,
            "WARN",
            f"found only off-PATH: {found}{note}",
            f"add to kinetic.toml under [tools]: {toml_line(tool, chosen.path)}",
            data,
        )
    status: Status = "INFO" if tool in OPTIONAL else "PASS"
    if missing:
        status = "WARN"
    return Check(cid, title, status, found + note, None, data)


def check_shadowing(tool: str, candidates: Sequence[Candidate]) -> Check | None:
    """Check 2: the first PATH hit is older than another installed copy."""
    if len(candidates) < 2:
        return None
    cid, title = f"shadow.{tool}", f"{tool} PATH shadowing"
    shadow = find_shadowing(candidates)
    if shadow is None:
        return Check(
            cid, title, "PASS", f"{len(candidates)} candidates; no older copy first on PATH"
        )
    first, best = shadow.first_path, shadow.best
    data = {"first_path": first.to_dict(), "best": best.to_dict()}
    detail = f"{_label(first)} shadows {_label(best)}"
    chosen = resolve(candidates)
    if chosen is not None and chosen.source in ("config", "env"):
        return Check(
            cid, title, "INFO", detail + f"; Kinetic uses the {chosen.source} pin", None, data
        )
    first_dir, best_dir = str(Path(first.path).parent), str(Path(best.path).parent)
    if best.source == "path":
        remedy = f"move PATH entry {best_dir} above {first_dir} (then open a new terminal)"
    else:
        remedy = f"add {best_dir} to PATH ahead of {first_dir} (then open a new terminal)"
    remedy += f"; or pin it in kinetic.toml [tools]: {toml_line(tool, best.path)}"
    return Check(cid, title, "WARN", detail, remedy, data)


def check_version(tool: str, candidates: Sequence[Candidate]) -> Check | None:
    """Check 3: the resolved tool's version is inside Kinetic's compatibility range."""
    spec = COMPAT.get(tool)
    chosen = resolve(candidates)
    if spec is None or chosen is None:
        return None
    cid, title = f"version.{tool}", f"{tool} version"
    want = describe_range(spec)
    data = {"version": chosen.version, "range": want, "path": chosen.path}
    if not chosen.version:
        return Check(cid, title, "WARN", f"unknown ({chosen.error}); want {want}", None, data)
    if version_ok(chosen.version, spec):
        return Check(cid, title, "PASS", f"{chosen.version} (want {want})", None, data)
    status: Status = "FAIL" if tool in REQUIRED else "WARN"
    remedy = f"install {tool} {want} and pin it in kinetic.toml [tools]"
    return Check(cid, title, status, f"{chosen.version} is outside {want}", remedy, data)


# ---------------------------------------------------------------- 4: Python env


def check_python_env() -> list[Check]:
    """Check 4: the running interpreter, and whether `manim` is importable in it."""
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    in_venv = sys.prefix != sys.base_prefix
    data = {"version": version, "executable": sys.executable, "venv": in_venv}
    where = "venv" if in_venv else "system interpreter"
    if (v.major, v.minor) == (3, 12):
        interp = Check(
            "python.env", "Python interpreter", "PASS", f"{version} ({where})", None, data
        )
    else:
        interp = Check(
            "python.env",
            "Python interpreter",
            "FAIL",
            f"{version} ({where}); Kinetic needs 3.12",
            "run Kinetic through `uv run` so the pinned 3.12 interpreter is used",
            data,
        )
    spec = importlib.util.find_spec("manim")
    if spec is not None:
        manim = Check("python.manim", "manim importable", "PASS", "importable in this environment")
    else:
        manim = Check(
            "python.manim",
            "manim importable",
            "WARN",
            "not importable in this environment (needed from M4)",
            "uv sync --extra manim",
        )
    return [interp, manim]


# ---------------------------------------------------------------- 5-8: hardware


def check_gpu(gpus: Sequence[Gpu], error: str | None) -> Check:
    data = {
        "gpus": [g.to_dict() for g in gpus],
        "has_discrete_gpu": any(g.discrete for g in gpus),
        "vendors": sorted({g.vendor for g in gpus}),
    }
    if error and not gpus:
        return Check("gpu", "GPU", "INFO", f"unknown ({error})", None, data)
    names = "; ".join(f"{g.name} (driver {g.driver})" if g.driver else g.name for g in gpus)
    kind = "discrete GPU present" if data["has_discrete_gpu"] else "no discrete GPU"
    return Check("gpu", "GPU", "INFO", f"{names or 'none reported'}; {kind}", None, data)


def check_encoders_listed(listed: set[str], error: str | None, deep: bool) -> Check:
    present = [e for e in WANTED_ENCODERS if e in listed]
    absent = [e for e in WANTED_ENCODERS if e not in listed]
    data = {"listed": present, "not_listed": absent}
    if error:
        return Check("encoders", "ffmpeg encoders", "WARN", error, None, data)
    detail = f"listed: {', '.join(present) or 'none'}"
    if absent:
        detail += f"; not in build: {', '.join(absent)}"
    if "libx264" not in listed:
        return Check(
            "encoders",
            "ffmpeg encoders",
            "FAIL",
            detail,
            "install an ffmpeg build with libx264 (e.g. the Gyan 'full' build)",
            data,
        )
    if not deep:
        detail += " (listed does not mean usable; --deep test-encodes each)"
    return Check("encoders", "ffmpeg encoders", "INFO", detail, None, data)


def check_encoder_usable(encoder: str, result: ProbeResult) -> Check:
    cid, title = f"encode.{encoder}", f"{encoder} test encode"
    data = {"usable": result.ok}
    if result.ok:
        return Check(cid, title, "PASS", result.detail, None, data)
    status: Status = "FAIL" if encoder == "libx264" else "WARN"
    return Check(cid, title, status, result.detail, None, data)


def check_bt709(result: ProbeResult) -> Check:
    if result.ok:
        return Check(
            "color.bt709", "BT.709 delivery tags", "PASS", result.detail, None, result.data
        )
    return Check(
        "color.bt709",
        "BT.709 delivery tags",
        "FAIL",
        result.detail,
        "the delivery chain must include setparams=colorspace/primaries/trc=bt709 (see M0)",
        result.data,
    )


def check_cpu_ram(cpus: int | None, ram_gb: float | None, tier: str) -> Check:
    ram = f"{ram_gb:.1f} GB RAM" if ram_gb is not None else "RAM unknown"
    data = {"cpu_count": cpus, "ram_gb": ram_gb, "tier": tier}
    return Check(
        "cpu_ram", "CPU and RAM", "INFO", f"{cpus} logical CPUs, {ram}; tier {tier}", None, data
    )


def check_disk(path: Path, free: float, total: float) -> Check:
    status = disk_status(free)
    data = {"path": str(path), "free_gb": round(free, 1), "total_gb": round(total, 1)}
    detail = f"{free:.0f} GB free of {total:.0f} GB on the cache volume ({path})"
    remedy = None
    if status != "PASS":
        remedy = "free disk space or set [cache] dir in kinetic.toml to a larger volume"
    return Check("disk", "Cache disk space", status, detail, remedy, data)


# ---------------------------------------------------------------- 9-12


def check_latex(latex: Sequence[Candidate], dvisvgm: Sequence[Candidate]) -> Check:
    have_latex, have_dvi = resolve(latex), resolve(dvisvgm)
    data = {"latex": have_latex.path if have_latex else None}
    data["dvisvgm"] = have_dvi.path if have_dvi else None
    if have_latex and have_dvi:
        return Check("latex", "LaTeX for MathTex", "PASS", "latex and dvisvgm found", None, data)
    return Check(
        "latex", "LaTeX for MathTex", "SKIP", "MathTex disabled; Text/Typst fallback", None, data
    )


def check_long_paths(enabled: bool | None) -> Check:
    if os.name != "nt":
        return Check("longpaths", "Windows long paths", "SKIP", "not Windows")
    if enabled is None:
        return Check("longpaths", "Windows long paths", "INFO", "LongPathsEnabled unreadable")
    detail = "LongPathsEnabled=1" if enabled else "LongPathsEnabled=0; keep cache paths short"
    return Check("longpaths", "Windows long paths", "INFO", detail, None, {"enabled": enabled})


def check_fonts() -> Check:
    return Check("fonts", "Style fonts", "SKIP", "no style configured")


def skip(cid: str, title: str, detail: str) -> Check:
    return Check(cid, title, "SKIP", detail)
