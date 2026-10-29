# Look development: failover (shot L1)

Same simulation, params and seed as `examples/smoke-failover`. 3.5 s of sim over 12 s (360 frames),
so the shot carries "time slowed 3.4×". Frame `f` shows tick `3500·f // 360`. The first frame at or after tick `T` is `ceil(T·360/3500)`.

**Treatment.** It is a dark stage lit by one overhead work light, with two cabinet-like nodes on plinths. The
links are physical conduits: the primary runs low in front and the secondary runs high behind, so the two read
apart even from a low camera. Nothing glows unless it carries information.

## Colour roles (style tokens `color.<role>`; overridable from the job's style)

| Role | Colour | Used for | Second encoding |
|---|---|---|---|
| `ground` | #16181b, falls off to #060708 | stage | – |
| `structure` | #5a6068 | node cabinets | – |
| `sender` / `receiver` | #8fd8ff / #f2e6cf | status strip on A / B | node label |
| `in_flight` | #46bfff | packet, the lit band on a conduit, an `up` link's hairline | motion; capsule (data) vs sphere (ack) |
| `standby` | #59606a | the secondary before the reroute | dashed hairline, no glow |
| `fault` | #ff6232 | the cut link, the lost packet | dashed hairline; status line |
| `annotation` | #e9e7e2 / #a3a9b1 | text, leaders, timeout ring | – |

Blue against orange holds up under deuteranopia. Fault is always dashed as well as coloured.

## What each motion encodes (trace-driven unless marked)

- **Packet position:** `(t − begin)/(end − begin)` of its resolved transit span, which is linear with no easing. The lost
  packet is the exception: see Limitations.
- **Lit band on the conduit (trail):** the stretch of conduit the packet covered in the last 150 ticks, so its length
  is proportional to speed. The ack (600 ms) has a longer trail than packet 2 (1212 ms) or packet 1 (1488 ms).
- **Link hairline:** the `status` of `link:P` / `link:S` (`up`, `standby`, `failed`, `down`).
- **Fault:** at `fault.injected` (tick 1200, f124) the primary turns fault-coloured and dashed. The lost packet turns
  orange where it was. The flash decays over ~70 ticks and the packet fades over 380 ticks; both envelopes are *illustrative*.
- **Timeout:** a ring spreads from A's footprint at `timeout.fired` (1500, f155). Its 520-tick envelope is *illustrative*.
- **Reroute:** the secondary's hairline goes from standby to up at `route.changed` (1600, f165).
- **Delivery and ack:** the B strip pulses when packet 2 is `delivered` (2812, f290), and the A strip pulses at
  `ack.received` (3412, f351). Delivered packets stay docked, unlit, in the receiving port.
- **Status line:** it changes only on trace events: "primary link cut, packet lost" (f124), "timeout: resend on
  secondary" (f155), "resend delivered" (f290).

## Camera (hero 16:9; *illustrative*, a pure function of the tick and trace state)

| Frames | Beat | Intent |
|---|---|---|
| 0–127 | lock-off, wide and low | establish both routes; packet 1 on the primary |
| 128–155 | push toward A (cut + 40 → cut + 300 ticks) | the cause moves to the sender; arrives as the timer fires |
| 165–289 | dolly along the secondary, driven by packet 2's progress (blend-in over its first 18 %) | follow the resend |
| 290–324, hold to 359 | settle wide, driven by the ack's progress | show the round trip complete |

The tall camera (9:16) looks down the link from A's side, so A is near and B is far, and runs the same beats.
`calm` uses three static framings with cuts: wide until the timeout, the secondary until delivery, then wide.

## Labels (screen space, ≤ 4 besides the badge)

The badge sits top-left, built from `job.evidence` and a scene qualifier. The time scale (`job.time_label`) is top-right,
or under the badge in 9:16. The entity labels are A, B, primary and secondary, with leader lines. They are scheduled by event: before the cut they
are A, B and primary. From the cut they are A and primary. From the timeout they are A and secondary. From packet 2's halfway point they are B and secondary. After delivery they are A and B.
Text is Godot's default font (no font files in the repo) at ≥ 40 px (1080p) or 56 px (1080×1920).

## Known limitations

- **Lost-span progress.** The span of a transit cut short ends at the loss tick, so its linear `progress` reaches
  1.0 at the cut and would draw packet 1 *arriving at B*. The scene places a lost packet at `(t − begin)/latency_ms`
  of its link instead, using the trace's `link:P.latency_ms`, which puts it at 81 % of the way at the cut. The
  annotation's `active_span.progress` still reports the span's own value. The contract should define progress for spans
  that end early.
- The model does not locate the cut on the link, so the whole primary changes state. No break point is invented.
- The push toward A is keyed to the cut and lasts 300 ticks, because the timeout tick is unknown until the timeout fires. In this trace it
  lands on the timeout exactly. With other params it would arrive early or late.
- `variant_calm: true` in film.yaml makes the pipeline *always* render calm (`use_calm = calm or shot.variant_calm`).
  So it is omitted here, and calm is rendered with `--calm`.
- The default font has no mono face, and the cabinet geometry is primitive boxes (no real bevels).
