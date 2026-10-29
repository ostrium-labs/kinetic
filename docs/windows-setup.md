# Windows setup

Kinetic is developed on Windows 11 (i5-1135G7, 15.7 GB RAM, Intel Iris Xe, no discrete GPU)
and tested on Windows and Ubuntu CI. This page lists what to install, where M0 found it, and
the fixes that were needed.

## 1. uv and Python

Kinetic needs uv >= 0.11 and uses the uv-managed Python 3.12 pinned by `.python-version`.
Install uv with winget (`astral-sh.uv`).

**PATH-order fix (M0).** On the development machine `where uv` returned
`...\Python312\Scripts\uv.exe` (0.11.31, a pip install) before the winget `uv.exe` (0.13.6).
The cause is user PATH order: the `Python312\Scripts` entry comes before
`...\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe`. Fix it by moving the
winget entry above `Python312\Scripts` in the user PATH. Uninstalling the pip copy
(`py -m pip uninstall uv`) is only needed if reordering is not enough.

Until PATH is fixed, call the winget binary directly:

```powershell
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe\uv.exe" --version
```

After `uv tool update-shell` or any PATH change, **open a fresh terminal**; running shells keep
the old PATH. Then check:

```powershell
uv --version            # expect 0.13.x
uv sync
uv run kinetic doctor   # reports any remaining shadowing as WARN
```

Always run Python through `uv run` so the project venv and its locked dependencies are used.
The system `python` (3.12.9) cannot import the project's dependencies.

## 2. Renderer and media tools

| Tool | Version | Location found in M0 | On PATH |
|---|---|---|---|
| Godot | 4.7.2 | `%LOCALAPPDATA%\Microsoft\WinGet\Packages\GodotEngine...\Godot_v4.7.2-stable_win64_console.exe` | folder yes, but no `godot` name |
| Blender | 5.2.3 LTS | `C:\Program Files\Blender Foundation\Blender 5.2\blender.exe` | no |
| FFmpeg / ffprobe | 9.0.2 (Gyan full build) | on PATH | yes |
| Kdenlive / melt | 26.08.2 | `%LOCALAPPDATA%\Programs\Kdenlive\bin\` | no (optional) |
| Manim | 0.22.1 | uv tool at `~\.local\bin\manim.exe`; becomes a project extra (`manim`) | n/a |

Notes:

- **Godot:** always use the `_console.exe` variant so stdout and stderr are captured. Never
  use `--headless` (it crashed and wrote no frames in M0). Movie Maker opens a real window;
  runs completed while unfocused. Output size comes from the project viewport, not from
  `--resolution` or the window (ADR 0005).
- **Blender:** background EEVEE works on Iris Xe through OpenGL. Expect a one-time shader
  compile of about 80 s.
- **FFmpeg:** working encoders were libx264, libsvtav1, ffv1, prores_ks, h264_qsv and
  hevc_qsv; av1_qsv, nvenc and amf do not work on this hardware. The Gyan full build is GPLv3;
  Kinetic invokes it and never redistributes it.

## 3. kinetic.toml

Tools that are not on PATH are configured per machine. Copy `kinetic.toml.example` to
`kinetic.toml` at the workspace root (it is git-ignored) and edit the paths:

```toml
[tools]
godot = "C:/Users/<you>/AppData/Local/Microsoft/WinGet/Packages/GodotEngine.GodotEngine_Microsoft.Winget.Source_8wekyb3d8bbwe/Godot_v4.7.2-stable_win64_console.exe"
blender = "C:/Program Files/Blender Foundation/Blender 5.2/blender.exe"

[cache]
dir = ".kinetic"
max_gb = 40
```

Resolution order: `kinetic.toml [tools]`, then `KINETIC_<TOOL>` environment variables
(`KINETIC_GODOT`, `KINETIC_BLENDER`, ...), then PATH, then known install locations. `kinetic
doctor` reports every candidate it finds, not only the first, and suggests `kinetic.toml`
entries for tools found off PATH. Use forward slashes in TOML paths.

## 4. LaTeX (optional)

LaTeX and dvisvgm are not installed and are not required. Without them Manim's `MathTex`,
the default `DecimalNumber` and `Axes(include_numbers=True)` fail; Kinetic uses `Text`-based
constructors instead, and `kinetic doctor` reports LaTeX as SKIP. MiKTeX (or Typst for SVG
equations) can be added later if a shot needs typeset equations.

## 5. Other checks

- **Disk:** keep at least 50 GB free on the cache volume (`doctor` warns below 50 GB and
  fails below 15 GB).
- **Long paths:** `doctor` reports `LongPathsEnabled` (read-only); Kinetic keeps cache paths
  short.
- **Line endings:** `.gitattributes` forces LF. Source hashes normalise CRLF to LF, so cache
  keys match across checkouts.
