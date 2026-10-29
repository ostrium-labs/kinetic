# Third-party tools and licenses

Kinetic's core (`src/ostrium_kinetic`) is Apache-2.0. It does not bundle or redistribute any of the tools below. It runs them as separate programs, in subprocesses, so their licenses do not extend to Kinetic or to the films it renders.

| Tool | How Kinetic uses it | License | Notes |
|---|---|---|---|
| Godot Engine 4.7 | Subprocess (Movie Maker) | MIT | Fonts and assets keep their own licenses |
| Blender 5.2 | Subprocess (`-b --python`) | GPL-2.0-or-later | `renderers/blender/` (added in M4) contains code that uses `bpy` and is licensed GPL-2.0-or-later. It never imports `ostrium_kinetic`; the two sides exchange only JSON files. Rendered images are not covered by the GPL. |
| Manim Community | Subprocess / optional extra | MIT | LaTeX is optional |
| FFmpeg | Subprocess | LGPL-2.1+ or GPL-3.0 depending on build | Never redistributed by Kinetic. H.264/HEVC patent-pool terms apply to paid or broadcast distribution of encoded content; AV1 and FFV1 are royalty-free. |
| Kdenlive / MLT | Optional, human finishing only | GPL / LGPL | Not in the automated path |

Python dependencies (pydantic, typer, ruamel.yaml, numpy, structlog, pytest, hypothesis, ruff, pyright) are permissively licensed (MIT, BSD, Apache-2.0, MPL-2.0 for hypothesis as a dev-only tool).
