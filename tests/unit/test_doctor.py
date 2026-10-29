"""Doctor checks over synthetic facts, and the report's exit code, JSON and text shape."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from ostrium_kinetic.env import checks as C
from ostrium_kinetic.env.checks import Check
from ostrium_kinetic.env.doctor import DoctorReport, run_doctor
from ostrium_kinetic.env.probes import ProbeResult
from ostrium_kinetic.env.tools import Candidate
from ostrium_kinetic.workspace import Workspace


def _real(tmp_path: Path, name: str) -> str:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return str(path)


def test_resolution_required_missing_fails() -> None:
    assert C.check_resolution("ffmpeg", []).status == "FAIL"
    assert C.check_resolution("python", []).status == "FAIL"
    assert C.check_resolution("godot", []).status == "WARN"
    assert C.check_resolution("typst", []).status == "SKIP"


def test_resolution_off_path_warns_with_toml_line(tmp_path: Path) -> None:
    exe = _real(tmp_path, "Blender 5.2/blender.exe")
    check = C.check_resolution("blender", [Candidate(exe, "known", "5.2.3")])
    assert check.status == "WARN"
    assert check.remedy is not None
    assert f'blender = "{Path(exe).as_posix()}"' in check.remedy


def test_resolution_on_path_and_pinned_pass(tmp_path: Path) -> None:
    exe = _real(tmp_path, "uv.exe")
    assert C.check_resolution("uv", [Candidate(exe, "path", "0.13.6")]).status == "PASS"
    assert C.check_resolution("uv", [Candidate(exe, "config", "0.13.6")]).status == "PASS"
    assert C.check_resolution("melt", [Candidate(exe, "path", "7.43.0")]).status == "INFO"


def test_resolution_flags_missing_pin(tmp_path: Path) -> None:
    exe = _real(tmp_path, "ffmpeg.exe")
    cands = [Candidate(str(tmp_path / "gone.exe"), "config"), Candidate(exe, "path", "9.0.2")]
    check = C.check_resolution("ffmpeg", cands)
    assert check.status == "WARN" and "missing file" in check.detail


def test_shadowing_check_m0_case(tmp_path: Path) -> None:
    old = _real(tmp_path, "Python312/Scripts/uv.exe")
    new = _real(tmp_path, "WinGet/astral-sh.uv_x/uv.exe")
    check = C.check_shadowing(
        "uv", [Candidate(old, "path", "0.11.31"), Candidate(new, "path", "0.13.6")]
    )
    assert check is not None and check.status == "WARN"
    assert old in check.detail and new in check.detail
    assert check.remedy is not None and check.remedy.startswith("move PATH entry")
    assert "uninstall" not in check.remedy


def test_shadowing_is_info_when_pinned(tmp_path: Path) -> None:
    pin = _real(tmp_path, "pin/uv.exe")
    old = _real(tmp_path, "a/uv.exe")
    new = _real(tmp_path, "b/uv.exe")
    cands = [
        Candidate(pin, "config", "0.13.6"),
        Candidate(old, "path", "0.11.31"),
        Candidate(new, "path", "0.13.6"),
    ]
    check = C.check_shadowing("uv", cands)
    assert check is not None and check.status == "INFO"
    assert C.check_shadowing("uv", cands[:1]) is None


def test_version_check(tmp_path: Path) -> None:
    exe = _real(tmp_path, "tool.exe")
    ok = C.check_version("godot", [Candidate(exe, "path", "4.7.2")])
    assert ok is not None and ok.status == "PASS"
    old_ffmpeg = C.check_version("ffmpeg", [Candidate(exe, "path", "6.1")])
    assert old_ffmpeg is not None and old_ffmpeg.status == "FAIL"
    new_godot = C.check_version("godot", [Candidate(exe, "path", "4.8.0")])
    assert new_godot is not None and new_godot.status == "WARN"
    unknown = C.check_version("uv", [Candidate(exe, "path", None, "could not read")])
    assert unknown is not None and unknown.status == "WARN"
    assert C.check_version("melt", [Candidate(exe, "path", "7.43.0")]) is None


def test_disk_check() -> None:
    assert C.check_disk(Path("."), 208, 477).status == "PASS"
    assert C.check_disk(Path("."), 40, 477).status == "WARN"
    failing = C.check_disk(Path("."), 10, 477)
    assert failing.status == "FAIL" and failing.remedy


def test_encoder_checks() -> None:
    assert C.check_encoders_listed(set(), None, False).status == "FAIL"
    listed = C.check_encoders_listed({"libx264", "h264_qsv"}, None, False)
    assert listed.status == "INFO" and "does not mean usable" in listed.detail
    bad = ProbeResult(False, "unusable: Cannot load nvcuda.dll")
    assert C.check_encoder_usable("h264_nvenc", bad).status == "WARN"
    assert C.check_encoder_usable("libx264", bad).status == "FAIL"
    assert C.check_bt709(ProbeResult(False, "tags wrong")).status == "FAIL"


def test_latex_skip_message() -> None:
    check = C.check_latex([], [])
    assert check.status == "SKIP" and check.detail == "MathTex disabled; Text/Typst fallback"


def _report() -> DoctorReport:
    return DoctorReport(
        [
            Check("tool.ffmpeg", "ffmpeg executable", "PASS", "9.0.2"),
            Check("shadow.uv", "uv PATH shadowing", "WARN", "old shadows new", "move PATH entry"),
            Check("fonts", "Style fonts", "SKIP", "no style configured"),
        ],
        {"tier": "laptop-igpu"},
    )


def test_report_exit_code() -> None:
    report = _report()
    assert report.exit_code == 0
    report.checks.append(Check("disk", "Cache disk space", "FAIL", "10 GB free"))
    assert report.exit_code == 1


def test_report_json_shape() -> None:
    data = json.loads(json.dumps(_report().to_dict()))
    assert {"version", "platform", "checks", "hardware"} <= set(data)
    assert data["hardware"] == {"tier": "laptop-igpu"}
    assert [c["id"] for c in data["checks"]] == ["tool.ffmpeg", "shadow.uv", "fonts"]
    assert set(data["checks"][0]) == {"id", "title", "status", "detail", "remedy", "data"}
    assert data["summary"]["WARN"] == 1


def test_render_text_lists_every_check() -> None:
    report = _report()
    text = report.render_text()
    for check in report.checks:
        assert check.id in text
    assert "-> move PATH entry" in text
    assert "Exit code 0" in text


@pytest.mark.renderer("ffmpeg")
@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_run_doctor_quick_on_this_machine(tmp_path: Path) -> None:
    ws = Workspace(tmp_path, None, {})
    report = run_doctor(ws)
    ids = {c.id for c in report.checks}
    assert {"tool.ffmpeg", "python.env", "gpu", "encoders", "cpu_ram", "disk", "fonts"} <= ids
    assert next(c for c in report.checks if c.id == "headless.godot").status == "SKIP"
    hw = json.loads((tmp_path / ".kinetic" / "hardware.json").read_text("utf-8"))
    assert {"cpu_count", "ram_gb", "gpus", "tier"} <= set(hw)
    json.dumps(report.to_dict())
