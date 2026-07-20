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

## Reproduction (2026-07-18)

TODO follow-up #1 (2026-07-11) asked to re-measure accruals on the **original 195-name broad
universe** before citing the old +0.036/t=2.1 number. That universe is **permanently
unreproducible** — its composition and results doc were gitignored and are gone (see "Why
re-measured" above), so the original number can never be re-derived. This follow-up is
therefore closed on the *reproducible* universes instead, which is what the disable decision
already rested on.

Re-ran the identical measurement six days later on current code (`--source xbrl`, keyless
companyfacts, `price_asof` 2026-07-18) to confirm the verdict is stable, not a stale artifact:

| universe | h=1 | h=3 | h=6 | h=12 |
|---|---|---|---|---|
| **largecap** (79) | +0.0132 (t=1.19, hit .54) | +0.0205 (t=1.14, hit .55) | +0.0347 (t=1.30, hit .66) | +0.0479 (t=1.46, hit .62) |
| **combined** (229) | −0.0002 (t=−0.03, hit .48) | −0.0008 (t=−0.05, hit .48) | −0.0018 (t=−0.07, hit .50) | −0.0173 (t=−0.54, hit .38) |

Both rows **reproduce the 2026-07-12 table to the reported precision** (the small
universe-size drift — 79/229 vs 79/231 — is a few small/mid names whose CIK no longer resolves
in `company_tickers.json`: TOWN, CIVI, VTLE, MMC, AGCO; it does not move the ICs). Accruals is
positive-signed but sub-significant on largecap (XS t ≤ 1.46 at every horizon; its only
above-threshold read is a **TS-IC** t=2.62 @h6 — a weak large-cap *time-series* tilt, never a
cross-sectional ranking edge) and flat-to-negative on combined. The XS-IC bar a ranking leg
must clear (positive **and** t>~2 at h≤6 on a reproducible universe) is missed everywhere.

**Verdict stands: DISABLED.** No further re-measurement is warranted — the enabling universe is
gone and every surviving reproducible universe agrees. Closed.

(Commands: `shortlist-backtest --source xbrl --universe largecap --horizons 1,3,6,12 --json`
and the same with `--tickers <largecap∪smallmid CSV>`. Raw JSON in the session scratchpad.)
