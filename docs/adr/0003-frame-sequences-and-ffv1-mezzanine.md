# ADR 0003: Per-shot frame sequences, converted to FFV1 mezzanines

**Status:** Accepted (M1, from M0 measurements, 2026-10-30)

## Context

Renders on this laptop are slow (Blender EEVEE about 9 s/frame at 1080p/64 spp) and can crash
or be interrupted. QA needs frame-level access: counts, dimensions, black and frozen ranges,
checkpoint stills. Delivery needs correct BT.709 tagging.

## Decision

- Each shot renders to a PNG frame sequence. After frame QA it is converted to an FFV1/MKV
  mezzanine, and the PNGs are deleted. Direct MP4 is used only for preview.
- The master is assembled from mezzanines with the concat demuxer, then encoded to delivery
  formats (`film.mp4`: H.264 High, yuv420p, BT.709 tagged, CRF 18).
- The BT.709 chain includes `setparams` plus the output tag flags. QA asserts
  `color_space`, `color_primaries`, `color_trc` and `color_range` with ffprobe.
- Preview encodes may use `h264_qsv`; delivery uses `libx264`.

## Consequences

M0 measurements (Gyan FFmpeg 9.0.2, Iris Xe):

- Working encoders: libx264, libsvtav1, ffv1, prores_ks, h264_qsv, hevc_qsv. Not working:
  av1_qsv (no AV1 hardware encode on Tiger Lake), nvenc, amf.
- FFV1 picks `bgr0` (lossless RGB). Concat of FFV1 segments was bit-exact by decoded MD5.
- Without `setparams`, primaries and transfer stayed `unknown`. With it, a #3A7BD5 patch
  decoded within +/-1 per channel; an untagged encode was off by up to 7.
- Disk on noisy test content: PNG about 209 KB/frame, FFV1 about 238 KB/frame (about 7.1 MB/s
  at 1080p30). A 3.5-minute film's mezzanines come to roughly 1.5 GB or less.
- Crash recovery is per frame for Blender and Manim; short Godot shots re-render whole.
- Frames never go into Git LFS. The cache has an LRU cap (`cache.max_gb`).
