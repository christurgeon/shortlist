# Accruals quality leg — re-measured and DISABLED

**Date:** 2026-07-12 · **Change:** `quality.earnings_quality.accruals: true → false`
(both earnings-quality legs now measured-but-not-scored). Committed evidence of record —
the original enablement artifact was gitignored and is gone; this doc exists so the
disable decision does not evaporate the same way.

## Why re-measured

The accruals leg (Sloan 1996 — earnings backed by cash persist, accrual-driven earnings
reverse; inverted so cash-backed names score higher) was **enabled 2026-06-21** on a
single ad-hoc **195-name** universe: XS-IC **+0.036 (t=2.1) @3m**, +0.059 (t=1.6) @12m,
hit 60–69%. That universe's composition and its results doc
(`2026-06-25-xbrl-broad-universe-results.md`) are gitignored and no longer exist on disk —
the number was **unreproducible**. It is an ACTIVE leg (moves live quality/composite), so a
reproducible verdict was needed.

## Measurement (2026-07-12, `--source xbrl`, keyless companyfacts, h=1/3/6/12)

Cross-sectional rank-IC (the axis a ranking leg must earn), accruals:

| universe | h=1 | h=3 | h=6 | h=12 |
|---|---|---|---|---|
| **largecap** (79, validation-era) | +0.013 (t=1.19, hit .54) | +0.021 (t=1.14, hit .55) | +0.035 (t=1.30, hit .66) | +0.048 (t=1.46, hit .62) EXPL |
| **combined** (231, largecap∪smallmid) | −0.000 (t=−0.03, hit .48) | −0.001 (t=−0.05, hit .48) | −0.002 (t=−0.07, hit .50) | −0.017 (t=−0.54) EXPL |

(Commands: `shortlist-backtest --source xbrl --tickers <universe> --horizons 1,3,6,12
--json`, Yahoo pre-warmed, companyfacts month-cache warm. Raw JSON in the session
scratchpad; combined is the same run as `2026-07-11-combined-universe-xbrl-ic-results.md`.)

## Verdict: DISABLE

Pre-committed rule: earns its enabled slot only if XS-IC is positive **and** t>~2 on a
reproducible universe at a trust-passing horizon (h≤6).

- **largecap**: positive-signed and direction-correct at every horizon (hit-rate up to
  0.66) — consistent with the original prior — but **sub-significant everywhere** (t ≤ 1.30
  at h≤6). The original t=2.1 does **not** replicate at the significance bar.
- **combined/small-mid**: **flat-to-negative** (XS-IC ~0.000). The edge does not generalize
  beyond large caps, yet the leg was applied to the whole universe, adding noise to the
  small/mid names.

So it fails the bar on every reproducible universe. Not pure noise — a weak, segment-specific
large-cap tilt — but too weak and too narrow to justify moving live scores universe-wide. The
math itself is unchanged and verified stable (`stats.py` Sloan formula; the edgartools
`NetIncomeLoss→NetIncome` concept-drift was neutralized in #74), so this is a genuine
signal-strength verdict, not a plumbing artifact.

`asset_growth` remains OFF (XS-IC −0.006, t=−0.3 — never had an edge). Both legs stay
**measured** in the backtest, just not scored.

## Process note

This is the second enabled-signal whose evidence-of-record had evaporated into a gitignored
`docs/superpowers/specs/` doc (the buyback KILL, 2026-07-11, was the first). Any signal that
moves live scores should record its evidence under the tracked `docs/audits/` tree. Filed as
a standing follow-up.
