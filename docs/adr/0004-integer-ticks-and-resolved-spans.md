# ADR 0004: Integer ticks and resolved spans in the trace

**Status:** Accepted (M1, 2026-10-30)

## Context

Renderers must agree on what is on screen at every frame, on Windows and on Linux. They must
show continuous motion (a packet in transit) without simulating lookahead. Floating-point
time drifts and can differ across platforms, and `state_at(t)` alone cannot describe motion
between events.

## Decision

- Trace time is integer ticks. Each trace declares `tick_rate` (seconds per tick) as an exact
  rational. Seconds are a presentation format only; `world.ticks()` rejects a value that is
  not a whole number of ticks.
- Film fps is a `Fraction`. Frame `f` of a shot maps to tick
  `start + floor((end - start) * f / frames)` using exact integer arithmetic.
- Events are totally ordered by `(tick, seq)`, where `seq` is the recorder's emission counter.
  JSONL is canonical: sorted keys, no whitespace, UTF-8, LF.
- Spans (`span.begin` / `span.end`) are resolved after the run into `spans.jsonl` with
  `begin_tick`, `end_tick`, `outcome` (`completed|lost|aborted`) and a closed interpolation
  enum: `step`, `linear`, or `curve:<name>` with baked keys.
- Randomness comes from named PCG64 streams (`RngStreams`); jitter uses integer draws.

## Consequences

- `state_at(tick)` folds every event with `event.tick <= tick`. A span is active on
  `[begin_tick, end_tick)`. Every renderer runtime must use exactly these boundaries.
- Renderers may not ease claim-bearing progress. A non-linear progress is modelled as a curve
  in the simulation.
- The smoke-failover trace (seed 7) has a committed sha256 in
  `tests/golden/smoke-failover.trace.sha256`, checked on Windows and Ubuntu CI. Changing it is
  a contract change.
- Determinism is defined at the trace, plan and annotation level. Pixels are compared only on
  the same machine class.
