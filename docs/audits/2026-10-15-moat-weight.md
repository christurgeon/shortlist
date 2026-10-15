# 2026-10-15 — does `moat` earn its 0.18 composite weight?

`moat` XS rank IC was negative on both universes at every horizon in
`2026-09-25-risk-tilt-disable.md` and was kept on magnitude and role. That note asked for a
pre-registered `no_moat` variant before any weight change. This is it.

## Pre-registration (written and committed before the run)

- **Variant.** `no_moat` = the shipped weights with `moat` removed: quality .18, growth .135,
  value .22, momentum .08 (risk is 0.0 in production, insider has no history).
  **Baseline** is `no_risk`, which is the shipped composite on the reconstructable axes.
- **Run.** `docs/audits/scripts/probe_composite_weights.py` on `largecap` and `smallmid`,
  horizons 1/3/6/12, engine breadth floor, excess returns. Output in
  `raw-2026-10-15-moat/`.
- **Rule — set `weights.moat` to 0.0 only if all three hold:**
  1. `moat` standalone XS IC is negative on **both** universes at h = 1, 3 and 6;
  2. on at least one universe, some h ≤ 6 reaches t ≤ −2 (the magnitude argument that kept
     moat in 2026-09-25 then fails);
  3. paired `comp_no_moat − comp_no_risk` mean is positive on both universes at h = 1, 3, 6.
- **Otherwise** the weight stays at 0.18 and the question is closed. A paired gain with
  t < 2 does not justify a change on its own (the 2026-09-25 bar).
- h = 12 is reported, not used: too few non-overlapping blocks.

## Result

_Pending run._
