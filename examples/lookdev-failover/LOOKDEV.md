# Look development: failover (shot L1)

Same simulation, params and seed as `examples/smoke-failover`. 3.5 s of sim over 12 s (360 frames),
so the shot carries "time slowed 3.4×". Frame `f` shows tick `3500·f // 360`. The first frame at or after tick `T` is `ceil(T·360/3500)`.

**Treatment (Gate B polish pass).** A dark, warm-graphite stage under one high lab lamp, shot like a
science documentary. The two nodes are rack-style enclosures built procedurally (`scenes/godot/lib/geo.gd`):
chamfered body, kick base, lid seam, a protruding bezel around a dark inset panel, three bays of chamfered
sleds, handles, side seams, a status slot near the top, and a socket plate with collar and screws on the
inner face where each cable plugs in. The links are sheathed cables that sag slightly between the sockets.
They have strain-relief boots and slim stands. A cable's inner core glows only where data is in flight.
The primary runs low in front and the secondary high behind. Nothing glows unless it carries information.

## Colour roles

All roles are defined once in `STYLE` at the top of `lookdev.gd`. Each one can be overridden from the job's style as `color.<role>`.

| Role | Colour | Used for | Second encoding |
|---|---|---|---|
| `ground` / `backdrop` | #171512 / #0b0a09 | floor (value-noise grain in albedo and roughness, radial falloff), far wall | – |
| `enclosure` / `enclosure_trim` / `enclosure_panel` | #2a2c30 / #3b3d41 / #1c1d20 | anodised body (rough 0.48, metal 0.35), bezel and handles (0.36 / 0.65), inset panel | – |
| `in_flight` | #7ee3ff | packet core and the lit core band of a cable; AgX rolls the core to near-white cyan | motion; capsule (data) vs sphere (ack) |
| `fault_hot` → `fault` | #ff7a1a → #e2452c | failed primary (amber at the cut, cooling to red), lost packet, break ember | **full-circumference dashed bands**, dashed status marker |
| `recovered` | #5fd1a8 | stripe on the link named by `route.changed.via` while `up`; receipt pulses; docked packets | wider solid stripe; status dot |
| `link_idle` | #8d97a3 | thin stripe on an `up` link that is not the rerouted route | solid hairline |
| `standby` | #4d5258 | the secondary before the reroute | dashed hairline, no glow |
| `annotation` / `annotation_dim` | #f1ebe0 / #bdb5a8 | text, leaders, timeout ring, idle status slots | – |
| `badge` | #a39b8e | evidence badge frame and qualifier, time label | fixed corner, mono |
| `key_light` / `rim_light` | #ffe4c4 / #8fb3ff | key spot / two narrow rim spots | – |

Cool data against amber-red fault holds up under deuteranopia. Fault is never hue-only, because it is always dashed. Recovered teal vs
data cyan is separated by role and shape: a stripe along the cable vs a moving capsule.

**Light.** A strong warm-neutral key spot sits high front-left (energy 170, 30°, shadows). Two narrow cool rim spots from behind
pick out the chamfers and cable tops without washing the floor. A very low neutral fill (0.14) comes from camera side, and a dim cool wash
falls on the far wall. Ambient is 0.03. AgX tonemap: exposure 1.05, white 12, contrast 1.15, saturation 0.92. SSAO is on. Glow is normalised,
threshold 1.2, levels 3–6, so only emissive data and fault bloom. Depth fog toward the warm-black backdrop. A gentle far depth of field
(from 16 m, amount 0.06) softens only the wall. A static elliptical vignette sits under the HUD. No volumetric fog, SSR, SDFGI or TAA, because they are history-dependent
or over budget.

## Typography

| Element | Face | Size (px at 1080 frame height) | Notes |
|---|---|---|---|
| Entity labels (`A · sender`, `B · receiver`, `primary`, `secondary`) | Noto Sans (SystemFont) | 40 | +0.6 px tracking (FontVariation); soft shadow |
| Status line | Noto Sans | 44 | centred, with a shape marker |
| Badge (2 lines), time label | Liberation Mono → DejaVu Sans Mono | 40 | no shadow; badge has a hairline frame |

9:16 multiplies every size by 1.43 (×1080/1080), so caps meet §12: about 28.6 px at 1080p and about 41 px at 1080×1920. The brief's
example scale (30/34/24 px) was raised to these sizes because §12 sets the minimum *cap height*, not the box height. Leaders are
1 px at 1080 (2 px at 1920). Each one is an elbow: a dot on the anchor, a vertical rise, then a short horizontal stub to the label. Margins are 64 px at 1080 on all
sides. 9:16 chrome starts below the top 14 %, and the status line ends at 80 %.
`A · sender` / `B · receiver` take the role from `Kinetic.state("node:X").role` at tick 0.
Fonts: Noto Sans and Liberation Mono are installed system fonts (OFL / SIL OFL 1.1). If they are missing, Godot's
SystemFont falls back to DejaVu Sans Mono and then to the OS default sans. No font files are added to the repo.

## Trace-driven vs illustrative

Trace-driven (state, spans or signals at the current tick):
- **Packet position:** the transit span's `planned_progress` (contract "Planned end"), mapped onto the cable *by arc length*
  so linear progress stays linear along the sagging cable. While a packet is in flight the value comes from `Kinetic.active_span`.
  After its span ends it comes from `Kinetic.last_span`, which is where it stopped: 1.0 when delivered, and 1200/1488 ≈ 0.81 for
  packet 1, which was cut short. The trail tail at an earlier tick uses the same formula on the resolved span.
- **Core glow (trail):** the stretch of cable the packet covered in the last 150 ticks, so its length is proportional to speed.
- **Link stripes:** the `status` of `link:P` / `link:S`. The link named in `route.changed`'s `via` attr is drawn `recovered`.
- **Fault:** at `fault.injected` (tick 1200, f124) the primary switches to dashed fault bands. The lost packet stops at the position
  where it was when the link was cut (81 % of the way, see Limitations) and turns fault-coloured.
- **Broken glow:** packet 1's trail freezes where its data stopped and an ember marks that point.
- **Timeout ring** at `timeout.fired` (1500, f155). **Reroute** at `route.changed` (1600, f165).
- **Receipts:** the B status slot pulses teal when packet 2 is `delivered` (2812, f290). The A slot pulses at `ack.received` (3412, f351).
- **Status line:** it changes only on trace events. "primary link cut · packet lost" (f124), "timeout · resend on secondary" (f155),
  "resend delivered" (f290), and "· acknowledged" is appended at `ack.received` (f351). The marker shape encodes state: two bars (fault),
  ring (timer fired), dot (delivered).

Illustrative (documented envelopes; they encode no quantity):
- Fault flash: decays over 70 ticks. Amber → red cooling: 260 ticks. Lost-packet dimming, cell dissolve and trail fade: 240 ticks.
- Timeout ring: 520 ticks. Receipt pulse: 220 ticks. Label fade-in: 60 ticks.
- Cable sag, braided jacket, stands, enclosure detail, vignette, depth of field.
- Camera easing: one curve family (smootherstep) throughout.

## Camera: every beat keyed to trace ticks

At `_ready` the scene reads `Kinetic.signals_in(-1, job.tick_end)` and the transit spans' begin/end ticks into `beats`. The
pose is then a pure function of the current tick.

| Beat | Starts / lands on (trace) | Hero 16:9 | Intent |
|---|---|---|---|
| establish | tick 0 → `fault.injected` | slow creep in, driven by packet 1's span progress | both routes; packet 1 on the primary |
| consequence | leaves at `fault.injected` (f124), lands at `timeout.fired` (f155) | push to a low 3/4 on A with the dead cable receding | the cause moves to the sender; the timer fires as the move lands |
| follow | `route.changed` / packet 2 span (f165 → f290) | dolly along the secondary driven by packet 2's progress (blend-in over its first 20 %) | follow the resend |
| settle | packet 2 `end_tick` (f290) → `ack.received` (f351), hold to f359 | pull back wide | the round trip complete |

9:16 (`tall`) runs the same beats. It looks down the link from A's side with a longer lens pitched about 18° down, so A is near and low,
B is far and high, and the set sits between the chrome and the caption band. **Calm** (`--calm`): three static framings, hard cuts
at `route.changed` (f165) and at packet 2's delivery (f290): wide, then the secondary, then wide.
The same push, lands and cuts would follow other params automatically, because nothing is a fixed tick offset any more.

## Labels (≤ 4 content labels; badge and time label are chrome)

The badge sits top-left and the time scale top-right (under the badge in 9:16). Entity labels are scheduled by trace event. Before the cut: A, B,
primary. From the cut: A, primary, status. From the timeout: A, secondary, status. From packet 2's halfway point: B, secondary,
status. After delivery: A, B, status. A label whose rect would leave the safe area is hidden for that frame instead of overlapping.

## Render times (Iris Xe, Forward+, wall clock of the Godot run)

| Profile | Seconds | Notes |
|---|---|---|
| preview 1280×720 | ~79 | most of the time is PNG encoding |
| production 1920×1080 | 286–290 | GPU 35–60 ms/frame, PNG encoding 190–690 ms/frame (with other work on the machine) |
| social 1080×1920 | 143–231 | same split |

**Over the ~3 min production budget.** The previous look encoded at about 190 ms/frame (90 s total). The new frames are about 3×
larger PNGs (≈ 0.6–0.8 MB vs ≈ 0.25 MB) because the floor grain, braided jacket, DOF and vignette gradients compress worse.
The most likely fix, not yet tried, is to drop the 37 /m floor-grain octave and the cable rib. No production, social or calm build was
completed after the switch to `planned_progress`. The lead re-scoped this example to a regression test (preview only).

## Known limitations

- **Lost-span progress (resolved).** The transit spans now carry `planned_end_tick`, so the scene no longer derives the
  lost packet's position from `link:P.latency_ms`. It uses `planned_progress`, and QA checks the annotated value against the trace.
  `progress` still reaches 1.0 at the cut, as the contract defines it.
- **The break point is where the data stopped, not where the cable was cut.** The model does not locate the cut on the link,
  so the sheath is never shown severed. The whole primary takes the dashed fault bands, and the ember and frozen trail sit at the lost packet's
  computed position. That position is trace-derived, but a viewer may read it as the cut location.
- The camera reads *future* signal ticks at `_ready`, so the push can land on `timeout.fired`. This is illustrative motion only; no
  on-screen state is shown before its tick.
- `variant_calm: true` in film.yaml makes the pipeline *always* render calm (`use_calm = calm or shot.variant_calm`).
  So it is omitted here, and calm is rendered with `--calm`.
- Labels use SystemFont, so another machine without Noto Sans / Liberation Mono renders a fallback face. Pixels are only
  guaranteed per machine class anyway (docs/qa.md).
- Enclosures are still rectilinear (chamfers, no fillets or perforations), and there is no floor reflection (SSR is out of budget).

## Change log (this pass)

1. Procedural chamfered enclosures with bezel, sled bays, handles, seams, status slot and socket plates (`lib/geo.gd`).
2. Sagging sheathed cables swept along a polyline, placed by arc length. Core-only data glow. Strain-relief boots.
3. Palette moved to warm graphite / cyan data / amber→red dashed fault / teal recovered, all as roles in one `STYLE` dict.
4. Lighting rebuilt: key spot, two rim spots, low fill, wall wash, AgX tuning, glow levels, far DOF, vignette.
5. Typography: Noto Sans + Liberation Mono via SystemFont, a type scale, elbow leaders with anchor dots, shape-coded status marker.
6. Camera keyed to trace beats read at `_ready`. 9:16 recomposed with a longer lens. Calm cuts on `route.changed` and delivery.
7. Loss: the packet stops at its stopped `planned_progress` (`last_span`), turns amber, then red, and dissolves (240 ticks). Its trail freezes and an ember marks the stop.
8. Fault read: the failed link gets full-circumference dashed bands, because a top hairline was too faint.
