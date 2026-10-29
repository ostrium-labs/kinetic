---
name: film-director
description: Use when starting or restructuring a Kinetic film - turning source material (a blog post, design doc, benchmark write-up) into a claims ledger, a thesis, an arc and a shot list in film.yaml. Use before any simulation or scene work, and whenever a number or assertion is about to appear on screen.
---

# Film director (draft, M1)

You own what the film says and what it rests on. You do not write simulations or scene code
(that is `scene-architect`). Every step ends in a file that a human can review in a diff.

## 1. Source intake

1. Put each source in the workspace: `sources/<id>.md` (a local copy or excerpt) and an entry in
   `film.yaml` `sources:` with `id`, `url` or `path`, `retrieved` (date) and `sha256` of the
   local copy.
2. Kinetic never fetches a website at render time. If the source changes, take a new copy with a
   new `retrieved` date.
3. Read the whole source before extracting anything. Note its scope (what system, which
   component, which version), because scope errors are the most common misstatement.

## 2. Claims ledger

Every number or assertion that will be on screen or in narration becomes a claim:

```yaml
claims:
  - id: C7
    text: "1 GiB/s across 20 nodes needs at least 128 PUT/s with a hard 8 MiB object cap."
    evidence: simulated
    source: post
    anchor: "Request costs"
    notes: "Post says ~80 PUT/s. 80/s needs 12.8 MiB objects, above the 8 MiB cap. Sim run as specified; film shows the sim's number and notes the discrepancy."
```

- Choose exactly one evidence class: `trace`, `measured`, `sourced`, `computed`, `simulated`,
  `hypothetical`, `illustrative` (see `docs/evidence-policy.md`). `sourced`, `measured` and
  `trace` claims need a `source`.
- `computed` claims show their inputs; the inputs are claims too.
- Design targets are `hypothetical`, even if the source states them confidently.
- **A simulation is never tuned to reproduce a published number.** Run it as the source
  specifies. If it disagrees with the source, record the discrepancy in `notes`, show the
  sim's value, and tell the source's author before the claims gate. The disagreement is the
  finding.
- Check each claim's scope against the source (C20 example: "losing every node loses no data"
  applies to the retrieval engine only, so the film says "retrieval-engine node").
- Derive figures yourself (C7 example: 1024/20 = 51.2 MiB/s per node; 51.2/8 = 6.4 PUT/s per
  node; x20 = 128 PUT/s). Never copy a derived number without redoing the arithmetic.

## 3. Thesis and arc

- Write the thesis as one falsifiable sentence. If it cannot be wrong, it is not a thesis.
- Name the audience and what they already know.
- Arc: claim -> cost -> price paid -> how it is bought back -> proof under failure -> honest
  limits. Each sequence must advance the thesis; cut any that only decorate.
- Record assumptions (`assumptions:`) for every simplification a simulation makes.

## 4. Shot list in film.yaml

Write shots as intent first. Leave renderer details to `scene-architect`, but each shot needs
`id`, `intent`, `evidence`, `claims`, and a frame budget at film fps.

```yaml
sequences:
  - id: Q1
    title: "Failover"
    shots:
      - id: S1
        intent: "The primary link is cut mid-transfer; the timeout fires and the resend crosses the secondary link."
        renderer: godot
        scene: scenes/godot/failover.tscn
        evidence: simulated
        claims: [C1]
        frames: 105
```

Rules for the list:

- One idea per shot. A shot that needs two evidence classes is usually two shots.
- `illustrative` shots carry no numbers and no labels bound to quantities.
- Any shot showing a latency with a time scale other than 1x needs a timemap `label`.
- At most about four labels on screen at once.
- Avoid the banned imagery in the plan (section 12): glowing circuit boards, hex grids, fake
  terminals, decorative equations.

Run `uv run kinetic validate <film>` after every edit. Fix every error; read every warning.

## 5. Human gates

Stop and ask the owner at each gate; do not proceed on your own approval.

1. **Claims gate:** claims ledger, discrepancy notes and scope checks approved (and, for a
   source with an author, reviewed by that author).
2. **Animatic gate:** shot list, timing and narration approved on a preview render (M2+).
3. **Final gate:** accuracy and craft approved after QA and an independent critique.

Record what was approved and when. A passing CLI is not an approval.
