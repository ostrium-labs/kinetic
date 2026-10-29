# Evidence policy

Every shot and every claim declares how we know what it shows. The evidence class is a closed
enum in the contract; on-screen badges make it visible to the viewer.

## The seven classes

| Class | Meaning | Example | On-screen badge |
|---|---|---|---|
| `trace` | Recorded from a real system run | a MySQL packet capture replayed as a trace | none; source in credits |
| `measured` | A benchmark result with a stated methodology | "22 ms p50 / 148 ms p99, Postgres bucket-WAL spike" | `MEASURED · <what>` |
| `sourced` | A published external figure | S3 $0.023/GB-month, AWS list price, retrieved 2026-09-22 | `LIST PRICE · <region>` (or the source kind) |
| `computed` | Deterministic arithmetic from cited inputs | 3 x $0.08 = $0.24 | none; the inputs are shown |
| `simulated` | A seeded model of the mechanism | read fall-through under node loss | `SIMULATED · model, not <system>` |
| `hypothetical` | A design target or scenario that is not built | "20-50 ms p99 target" | `DESIGN TARGET · not measured` |
| `illustrative` | No data semantics | an establishing shot | none; may carry no numbers |

## Rules

1. **Every on-screen number cites a claim.** Narration and on-screen text contain no number
   that is not a claim reference. Numbers keep their claim's precision and units; no invented
   decimals.
2. **Sources.** `sourced`, `measured` and `trace` claims must name a source (URL or local
   copy, retrieval date, sha256). Kinetic never fetches a source at render time.
3. **Computed claims** show their inputs, and each input is itself a claim.
4. **Simulations are never tuned to match a published number.** A simulation runs as the
   source specifies. If its result disagrees with the source, the film shows the
   simulation's result and the claim records the discrepancy. The disagreement is the
   finding, and the source's author is told before the claims gate.
5. **Scope.** A claim states the scope its source supports (which component, which version).
   Widening a claim's scope is a misstatement even when every number is right.
6. **Design targets are `hypothetical`,** even when the source states them as facts about the
   design. They carry the `DESIGN TARGET` badge.
7. **Illustrative shots carry no quantities.** No numeric labels, and no visual encoding of a
   quantity (size, speed or colour that implies a value). `kinetic validate` warns when an
   illustrative shot cites a quantitative claim.
8. **Renderers never invent state.** Motion that is not derived from the trace is
   illustrative and may not encode a quantity. Renderers may not ease the progress of
   claim-bearing entities.
9. **Time scale is disclosed.** When a shot's timemap is not 1x real time, the shot carries a
   label ("time slowed 10x"); `kinetic validate` makes this an error when it is missing.
10. **Badges are not optional.** `measured`, `sourced`, `simulated` and `hypothetical`
    content shows its badge (table above) whenever it is on screen. `trace` is credited at the
    end; `computed` shows its inputs instead; `illustrative` has no badge.
11. **Distribution shapes and other modelling choices are assumptions.** A latency
    distribution fitted to two percentiles is labelled "distribution shape assumed" and
    listed in `assumptions:`.

## Worked example: a discrepancy note

A post states that 1 GiB/s across 20 nodes, flushing every 250 ms or at 8 MiB, gives "about
80 PUTs a second". Per node that is 51.2 MiB/s. The 250 ms timer alone gives 80 PUT/s but
12.8 MiB objects, above the 8 MiB cap. With the cap as a hard maximum, the size cap fires
first: 51.2 / 8 = 6.4 PUT/s per node, 128 PUT/s in total. The claim records:

```yaml
- id: C7
  text: "At 1 GiB/s across 20 nodes with an 8 MiB cap, the WAL needs at least 128 PUT/s."
  evidence: simulated
  source: post
  anchor: "Request costs"
  notes: "Post says ~80 PUT/s; that needs 12.8 MiB objects, above the 8 MiB cap. Sim run as specified. Reported to the author. Request cost re-derived from the sim's rate."
```

## Gates

Claims are approved by a human (and by the source's author where there is one) before any
shot is built. QA (M2+) blocks any on-screen numeric annotation without a claim id, and an
independent critique checks badges, labels and illustrative elements before the final gate.
