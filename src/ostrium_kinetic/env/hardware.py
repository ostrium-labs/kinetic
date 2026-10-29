"""Machine facts for the doctor: CPU, RAM, GPUs, disk, long-path support, PNG headers."""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from ostrium_kinetic.env.tools import Runner, run

GB = 1024**3


@dataclass(frozen=True)
class Gpu:
    name: str
    vendor: str
    driver: str | None = None
    discrete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def gpu_vendor(name: str) -> str:
    lowered = name.lower()
    if "nvidia" in lowered or "geforce" in lowered or "quadro" in lowered:
        return "nvidia"
    if "amd" in lowered or "radeon" in lowered:
        return "amd"
    if "intel" in lowered:
        return "intel"
    if "apple" in lowered:
        return "apple"
    return "other"


def is_discrete(name: str) -> bool:
    """NVIDIA cards and AMD Radeon RX count as discrete; integrated graphics do not."""
    vendor = gpu_vendor(name)
    if vendor == "nvidia":
        return True
    return (
        vendor == "amd" and re.search(r"radeon\s+(?:\(tm\)\s+)?(rx|pro)\b", name, re.I) is not None
    )


def make_gpu(name: str, driver: str | None = None) -> Gpu:
    name = name.strip()
    return Gpu(name, gpu_vendor(name), driver, is_discrete(name))


def parse_win32_video(text: str) -> list[Gpu]:
    """Parse `Get-CimInstance Win32_VideoController | ConvertTo-Json` (object or list)."""
    text = text.strip()
    if not text:
        return []
    data = json.loads(text)
    items = data if isinstance(data, list) else [data]
    return [
        make_gpu(str(item.get("Name") or ""), item.get("DriverVersion"))
        for item in items
        if isinstance(item, dict) and item.get("Name")
    ]


def parse_lspci(text: str) -> list[Gpu]:
    """GPU lines from `lspci`: VGA, 3D and display controllers."""
    gpus = []
    for line in text.splitlines():
        match = re.search(
            r"(?:VGA compatible controller|3D controller|Display controller):\s*(.+)", line
        )
        if match:
            gpus.append(make_gpu(match.group(1)))
    return gpus


def list_gpus(runner: Runner = run) -> tuple[list[Gpu], str | None]:
    """Query the OS for video adapters. Returns (gpus, error)."""
    if sys.platform == "win32":
        cmd = (
            "Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion "
            "| ConvertTo-Json"
        )
        result = runner(["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd], 60.0)
        if result.returncode != 0:
            return [], f"Win32_VideoController query failed: {result.tail()}"
        try:
            return parse_win32_video(result.stdout), None
        except (ValueError, TypeError) as exc:
            return [], f"could not parse Win32_VideoController output: {exc}"
    lspci = shutil.which("lspci")
    if lspci is None:
        return [], "lspci not found"
    result = runner([lspci], 30.0)
    if result.returncode != 0:
        return [], f"lspci failed: {result.tail()}"
    return parse_lspci(result.stdout), None


def total_ram_bytes() -> int | None:
    """Physical memory: GlobalMemoryStatusEx on Windows, /proc/meminfo on Linux."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class MemoryStatusEx(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD),
                ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        status = MemoryStatusEx()
        status.dwLength = ctypes.sizeof(MemoryStatusEx)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return int(status.ullTotalPhys)
    try:
        return parse_meminfo(Path("/proc/meminfo").read_text("utf-8"))
    except OSError:
        return None


def parse_meminfo(text: str) -> int | None:
    match = re.search(r"^MemTotal:\s+(\d+)\s*kB", text, re.M)
    return int(match.group(1)) * 1024 if match else None


def hardware_tier(has_discrete_gpu: bool, ram_gb: float | None) -> str:
    """Coarse render-budget class: a laptop iGPU or a workstation."""
    if not has_discrete_gpu and (ram_gb is None or ram_gb < 24):
        return "laptop-igpu"
    return "workstation"


def disk_status(
    free_gb: float, warn_below: float = 50.0, fail_below: float = 15.0
) -> Literal["PASS", "WARN", "FAIL"]:
    if free_gb < fail_below:
        return "FAIL"
    if free_gb < warn_below:
        return "WARN"
    return "PASS"


def free_gb(path: Path) -> tuple[float, float]:
    """(free, total) GB on the volume holding `path`."""
    usage = shutil.disk_usage(path)
    return usage.free / GB, usage.total / GB


def long_paths_enabled() -> bool | None:
    """HKLM LongPathsEnabled (read-only). None when unreadable or not on Windows."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
    except OSError:
        return None
    return bool(value)


def png_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR chunk, or None if it is not a PNG."""
    try:
        with path.open("rb") as fh:
            head = fh.read(24)
    except OSError:
        return None
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", head[16:24])
    return width, height


def cpu_count() -> int | None:
    return os.cpu_count()
