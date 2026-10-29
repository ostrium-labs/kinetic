"""`kinetic doctor`: run every environment check and summarise the machine."""

from __future__ import annotations

import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ostrium_kinetic import __version__, log
from ostrium_kinetic.canonical import dumps, write_text_lf
from ostrium_kinetic.env import checks as C
from ostrium_kinetic.env import hardware, probes
from ostrium_kinetic.env.checks import Check
from ostrium_kinetic.env.tools import TOOLS, Candidate, Runner, discover_all, resolve, run
from ostrium_kinetic.workspace import Workspace

RESOLUTION_TOOLS = (
    "python",
    "uv",
    "ffmpeg",
    "ffprobe",
    "godot",
    "blender",
    "manim",
    "melt",
    "kdenlive",
    "typst",
)


@dataclass
class DoctorReport:
    checks: list[Check]
    hardware: dict[str, Any] = field(default_factory=dict)
    deep: bool = False

    @property
    def exit_code(self) -> int:
        return 1 if any(c.status == "FAIL" for c in self.checks) else 0

    def counts(self) -> dict[str, int]:
        return {s: sum(c.status == s for c in self.checks) for s in C.STATUSES}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "platform": sys.platform,
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "deep": self.deep,
            "summary": self.counts(),
            "exit_code": self.exit_code,
            "checks": [c.to_dict() for c in self.checks],
            "hardware": self.hardware,
        }

    def render_text(self) -> str:
        id_width = max((len(c.id) for c in self.checks), default=0)
        lines = [f"kinetic doctor {__version__} ({platform.system()} {platform.release()})", ""]
        for c in self.checks:
            lines.append(f"{c.status:<4}  {c.id:<{id_width}}  {c.title} - {c.detail}")
            if c.remedy:
                lines.append(f"{'':<4}  {'':<{id_width}}  -> {c.remedy}")
        counts = self.counts()
        summary = ", ".join(f"{n} {s}" for s, n in counts.items() if n)
        lines += ["", f"{summary}. Exit code {self.exit_code}."]
        if not self.deep:
            lines.append("Run `kinetic doctor --deep` for test encodes and renders.")
        return "\n".join(lines)


def _tool_checks(found: dict[str, list[Candidate]]) -> list[Check]:
    out: list[Check] = []
    for tool in RESOLUTION_TOOLS:
        out.append(C.check_resolution(tool, found[tool]))
    for tool in RESOLUTION_TOOLS:
        check = C.check_shadowing(tool, found[tool])
        if check is not None:
            out.append(check)
    for tool in RESOLUTION_TOOLS:
        check = C.check_version(tool, found[tool])
        if check is not None:
            out.append(check)
    return out


def _deep_checks(
    found: dict[str, list[Candidate]],
    listed: set[str],
    scratch: Path,
    runner: Runner,
) -> tuple[list[Check], list[str]]:
    """Test encodes, the BT.709 chain, and the Godot/Blender/Manim feasibility renders."""
    out: list[Check] = []
    usable: list[str] = []
    ffmpeg, ffprobe = resolve(found["ffmpeg"]), resolve(found["ffprobe"])
    if ffmpeg is not None:
        for enc in probes.WANTED_ENCODERS:
            if enc in listed:
                result = probes.try_encoder(ffmpeg.path, enc, runner)
                out.append(C.check_encoder_usable(enc, result))
                if result.ok:
                    usable.append(enc)
    if ffmpeg is not None and ffprobe is not None:
        work = scratch / "bt709"
        work.mkdir(parents=True, exist_ok=True)
        out.append(C.check_bt709(probes.check_bt709(ffmpeg.path, ffprobe.path, work, runner)))
    else:
        out.append(C.skip("color.bt709", "BT.709 delivery tags", "needs ffmpeg and ffprobe"))

    godot = resolve(found["godot"])
    if godot is None:
        out.append(C.skip("headless.godot", "Godot Movie Maker render", "godot not found"))
    else:
        out.append(
            _render_check(
                "headless.godot",
                "Godot Movie Maker render",
                probes.godot_render(godot.path, scratch, runner),
            )
        )
    blender = resolve(found["blender"])
    if blender is None:
        out.append(C.skip("headless.blender", "Blender EEVEE render", "blender not found"))
    else:
        out.append(
            _render_check(
                "headless.blender",
                "Blender EEVEE render",
                probes.blender_render(blender.path, scratch, runner),
            )
        )
    out.append(_manim_check())
    return out, usable


def _render_check(cid: str, title: str, result: probes.ProbeResult) -> Check:
    if result.ok:
        return Check(cid, title, "PASS", result.detail, None, result.data)
    return Check(cid, title, "WARN", result.detail, None, result.data)


def _manim_check() -> Check:
    import importlib.util

    if importlib.util.find_spec("manim") is None:
        return C.skip("headless.manim", "Manim render", "manim adapter arrives in M4")
    return Check(
        "headless.manim",
        "Manim render",
        "INFO",
        "manim importable; render probe arrives with the M4 adapter",
    )


def run_doctor(ws: Workspace, deep: bool = False, runner: Runner = run) -> DoctorReport:
    """Run all checks; with `deep`, also real encodes and renders. Writes <cache>/hardware.json."""
    logger = log.get(component="doctor")
    started = time.monotonic()
    cache = ws.cache()
    found = discover_all(ws, TOOLS, runner=runner)
    checks = _tool_checks(found)
    checks += C.check_python_env()

    gpus, gpu_error = hardware.list_gpus(runner)
    checks.append(C.check_gpu(gpus, gpu_error))

    ffmpeg = resolve(found["ffmpeg"])
    listed: set[str] = set()
    if ffmpeg is None:
        checks.append(C.skip("encoders", "ffmpeg encoders", "ffmpeg not found"))
    else:
        listed, enc_error = probes.list_encoders(ffmpeg.path, runner)
        checks.append(C.check_encoders_listed(listed, enc_error, deep))

    cpus = hardware.cpu_count()
    ram = hardware.total_ram_bytes()
    ram_gb = round(ram / hardware.GB, 1) if ram else None
    has_discrete = any(g.discrete for g in gpus)
    tier = hardware.hardware_tier(has_discrete, ram_gb)
    checks.append(C.check_cpu_ram(cpus, ram_gb, tier))

    free, total = hardware.free_gb(cache)
    checks.append(C.check_disk(cache, free, total))

    usable: list[str] | None = None
    if deep:
        scratch = ws.cache("doctor")
        try:
            deep_checks, usable = _deep_checks(found, listed, scratch, runner)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        checks += deep_checks
    else:
        for cid, title in (
            ("headless.godot", "Godot Movie Maker render"),
            ("headless.blender", "Blender EEVEE render"),
            ("headless.manim", "Manim render"),
        ):
            checks.append(C.skip(cid, title, "run with --deep"))

    checks.append(C.check_latex(found["latex"], found["dvisvgm"]))
    checks.append(C.check_long_paths(hardware.long_paths_enabled()))
    checks.append(C.check_fonts())

    hw: dict[str, Any] = {
        "cpu_count": cpus,
        "ram_gb": ram_gb,
        "gpus": [g.to_dict() for g in gpus],
        "has_discrete_gpu": has_discrete,
        "gpu_vendors": sorted({g.vendor for g in gpus}),
        "tier": tier,
    }
    if usable is not None:
        hw["usable_encoders"] = usable
    write_text_lf(cache / "hardware.json", dumps(hw) + "\n")

    for c in checks:
        logger.info("doctor.check", check=c.id, status=c.status, detail=c.detail)
    report = DoctorReport(checks, hw, deep)
    logger.info(
        "doctor.done", exit_code=report.exit_code, seconds=round(time.monotonic() - started, 1)
    )
    return report
