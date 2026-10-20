# Momentum Stage 0 "bound the prize" — the 2026-06-14 result and the re-run rule

Transcribed on 2026-10-20 from the module docstring of `src/shortlist/backtest/prize_bound.py`,
which held it after it moved there from `TODO.md` on 2026-09-07. The design notes were in the
gitignored `docs/superpowers/specs/`; this note is the tracked record.

## What the gate measures

Stage 0 of the multi-horizon momentum work. It decides, before any measurement harness is
built, whether swapping the momentum sub-score leg can move the composite shortlist at
momentum's ~0.08 weight. It re-blends each real ScoreCard's composite with the momentum leg
replaced, for a candidate-independent maximum-churn weight bound (A) and for each candidate (B),
then reports top-N overlap and Kendall tau against the real ranking.

## Result (2026-06-14): marginal PROCEED — not final

- Run on a 28-name SUBSET.
- `mom_12_1`: tau **0.947**.
- `mom_6m`: tau 0.995 against the incumbent `rel_strength_6m` — fully redundant.

The verdict is not final: the subset is small, and a quota-starved run inflates momentum's
effective weight.

## Rule for the re-run

Re-run on the full 80-name largecap basket and compare `mom_12_1`'s tau to the recorded 0.947.

- Holds or drops ⇒ the prize is real; write the Stage 1 plan.
- Rises toward 1.0 ⇒ **stop**: momentum at its 0.08 weight is a near-zero mover.
- Drop `mom_6m` either way.

The re-run costs ~1,000 FMP calls against a 250/day free cap, so it is scheduling-bound, not
host-bound. It waits on the FMP quota decision (`TODO.md` → "FMP quota is over-subscribed").
