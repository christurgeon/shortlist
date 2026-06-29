# TODO — follow-ups for a future session

Tracked, low-urgency follow-up work that has no natural home in code comments.
Newest context at top. See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for the signal designs and
`docs/ASSESSMENT_GAPS.md` for the broader scoring roadmap.

---

## Activist 13D discovery + selection ledger — Phase 2 follow-ups (2026-06-29)

Shipped (#93; CLAUDE.md "Activist 13D discovery + selection ledger", AUTONOMOUS_SCOUT §4):
the `EdgarActivist13DSignal` discovery originator (initial SCHEDULE 13D), the common-stock
CIK→ticker resolver, `quality.py` filters, the selection ledger + excess-over-SPY scoreboard,
and the `scout.daily_push.research: false` digest mode with a `/deep` block. **Discovery-only —
no scored leg** (it ships as a defensible prior; the ledger measures it). Deferred, in priority
order:

1. **Forward-return analysis of the ledger ← the whole point of the ledger.** Once daily picks
   accumulate, analyze the scoreboard: excess-over-SPY hit-rate at 1/3/6/12m and the
   `activist_13d` vs `edgar_form4` cohort split (the `catalyst` field). This converts the
   defensible-prior signal weight (1.5) into evidence and tests the after-close-drift thesis
   (Bebchuk-Brav-Jiang 2015). **Gated on accumulated picks** — arm `scout.daily_push.enabled`
   (+ `research: false` for the lean digest) and let the timer run.
2. **Pre-screen market-cap floor in the funnel.** v1 relies on the post-screen
   `below_min_mktcap` gate + the non-gated `/deep`-block filter, so a micro-cap shell can still
   consume one of the ~10/day FMP deep-screen slots before exclusion. A keyless pre-screen floor
   would protect the budget, but there's no clean keyless market-cap source (needs shares×price),
   so it was deferred (spec §14). Revisit only if budget pressure bites at 13D volume (~4-12/day).
3. **8-K + FINRA short-interest discovery originators.** Two VPS-safe originators to widen the funnel.
   - ✅ **FINRA short-interest jumps — SHIPPED** (`FinraShortInterestSignal`, default OFF; CLAUDE.md
     "Short-interest discovery (scout)"). Ships as a **CONTESTED prior** (adversarial PnL review:
     the jump is the *negative* signal — Cohen-Diether-Malloy; DTC a *stronger* negative predictor —
     Hong et al), so it's a middle-band attention signal at weight 0.5, default-disabled, and the
     selection ledger earns it a weight via a pre-registered promotion/kill rule (≥30 picks, 6m
     median excess-over-SPY ≥ 0). Remaining follow-ups: (a) **promotion/kill measurement** — gated
     on accumulated picks (arm `daily_push` + enable the signal, like item 1); (b) a cleaner
     **fund/ETF universe filter** than the seed `deny_list` (scorer abstention is today's backstop;
     the 5th-letter `*F/*Y/*W/*U/*R/*Q` drop catches OTC/derivatives but not 4-letter ETFs/CEFs);
     (c) **from-zero ramps** (brand-new short positions, currently dropped by `min_prev_short_shares`)
     as a separate absolute-share variant.
   - ⏳ **8-K originator — still deferred.** Curated 8-K item classes (1.01/8.01/5.02) from the SEC
     daily index — a one-file `SignalSource` against the existing interface (+ `daily.py` wiring),
     the `EdgarActivist13DSignal` precedent.
4. **SCHEDULE 13D/A amendment signal.** `scout.activist_13d.include_amendments` exists (off —
   amendments run ~20-46/day and are spammy), but a stake-*increase* amendment is a real
   escalation. A future version could surface only amendments that raise the stake materially.
   Measure before enabling.
5. **Stake-% extraction.** The % owned lives in the filing body, not the index/header, so v1
   doesn't parse it (strength is 0.7 + marquee/co-filer bumps). A larger stake is a stronger
   signal — parse it if item 1's cohort analysis shows it discriminates.
6. **Marquee-activist alias map** (`scout/quality.py:_MARQUEE`) is curated + non-exhaustive (fires
   on a minority of filings by design — substring-anywhere match, narrow filer-name space). Extend
   as new credible activists appear.

**Status:** not started — all deferred at ship. Item 1 is the keystone and is gated on accumulated
daily picks; 2-6 are independent. None are correctness blockers (the feature is complete + reviewed).

---

## §2 price-refinement axes — measured, NONE wired (2026-06-28)

Built three OHLCV-only price signals as **backtest-only measurement axes** on the live-price
`MomentumSignalSource` (the residual_momentum precedent): `pct_to_52w_high` (George-Hwang),
`max_daily_return` (Bali MAX-effect), `vol_scaled_momentum` (Barroso-Santa-Clara). Pre-registered
measurement on **both** bundled universes (largecap-79 + smallmid-152), full 1/3/6/12m grid, with
collinearity kills (≥0.5) and Phase-2 homes fixed in advance. Evidence of record:
`docs/superpowers/specs/2026-06-28-price-signal-bundle-results.md`.

**No config flip earned — all three parked** (the share_count/asset_growth precedent):
- **`pct_to_52w_high`: REJECT** — corr **+0.70/+0.74** vs the scored `price_vs_200dma` leg on both
  universes (a monotone re-skin of trend; EV/EBIT duplication trap); weak/negative IC anyway.
- **`vol_scaled_momentum`: REJECT** — corr **+0.52–0.54** vs scored momentum (duplicates *raw*
  momentum, which is XS-insignificant here); never |t|≥2. NOT a `residual_momentum` twin (+0.21–0.23),
  confirming the de-beta — not the vol-scaling — is residual momentum's edge. A null doesn't refute
  Barroso-Santa-Clara (their result is time-series vol-targeting, not XS alpha).
- **`max_daily_return`: PARK** — the only one orthogonal to existing signals (corr −0.07/−0.10), but
  the inverted-score IC **sign flips across universes** (NEG/significant in largecap — the MAX/lottery
  effect *reverses* in mega-caps; weakly POS in smallmid). Fails "≥1 universe without the other
  contradicting," so even the pre-registered defensive-flag home is unjustified. Revisit only via a
  small-cap-restricted or through-cycle test.

**In-scope fix shipped:** the collinearity diagnostic was gated to `--source xbrl`; now runs on the
`--source momentum` path too (so the residual~momentum + price-axis pairs are actually measured).

**Side benefit:** `residual_momentum` (the live leg) re-confirmed as the only short-horizon XS winner
on both universes (LC +0.023 t2.63, SM +0.025 t3.71). **Status:** done — axes are measurement-only,
no production wiring; Phase-2 deferred (nothing earned it).

---

## DEF 14A proxy — Phase 2 follow-ups (2026-06-28)

The DEF 14A proxy reader shipped as a research-only context line (#90; CLAUDE.md
"Proxy statement (DEF 14A) …", ASSESSMENT_GAPS §3.1). Deferred, in priority order:

1. **`pay_for_performance_alignment` backtest axis.** Unlike the other narrative research
   inputs, the proxy's Pay-vs-Performance table is *structured XBRL* (Item 402(v),
   `ProxyStatement.pay_vs_performance`), so it could clear the rank-IC bar and become a
   scored leg. Caveat: 402(v) uses ECD-taxonomy tags (`ecd:PeoActuallyPaidCompAmt` …) from
   the proxy's own XBRL — **confirm these are reachable from the companyfacts/XBRL backtest
   path** (they may not be; the fallback is the snapshot-replay path once accumulation
   captures proxy facts). Then measure rank IC + collinearity vs `quality`/`insider` before
   wiring or rejecting, like the PREDICTIVE_SIGNALS legs. Unfitted until measured.
2. **Narrative sections (related-party / CD&A).** Highest-value-but-unextractable in v1 (no
   edgartools section splitter; ~350K-char raw text) — needs custom section extraction to
   surface as quote-verifiable haystack text. Heavier build.

**Status:** not started — both deferred at ship; item 1 gated on confirming 402(v) ECD-tag
reachability (or on accumulated proxy-fact snapshots).

---

## negative_fcf excuse — measurement path (scope B, follow-up to #83) (2026-06-26)

#83 populated `fcf_positive` on the XBRL panel (`_xbrl_facts.panel_to_metrics`, with a
stale-FY abstention guard mirroring the bridge), clearing the **field-level** blocker. The
field is now correct on both paths — but the stage-aware `negative_fcf` excuse is **still
unmeasured**: `XbrlSignalSource` emits sub-score axes only and never calls `check_gates`, so
nothing reads `fcf_positive` in the backtest yet.

**Remaining (scope B):** a gate-impact backtest diagnostic — compare forward returns of
*excused* (high-growth) vs. *gated* negative-FCF names to test whether the excuse
(`revenue_cagr ≥ 0.15 ∧ persistence ≥ 0.70`) actually improves returns vs. a blanket gate.
New machinery (the XBRL source would need to evaluate the gate, or a parallel cohort path).
Thresholds stay unfitted priors until then. See `docs/ASSESSMENT_GAPS.md` §2.7.

---

## Broad-universe XBRL backtest — settled three unfitted priors (2026-06-25)

Ran `--source xbrl` keylessly on a new bundled **`smallmid`** universe (158 small/mid-caps,
`backtest/universe_smallmid.txt`) — the properly-powered cross-section the prior large-cap
piotroski null lacked. **No config flip earned.** Cross-sectional rank IC (the |t|≥2 bar):

- **`share_count` (→ `quality.dilution`): NULL on both universes** (XS |t|<0.8). Robust — the
  big large-cap TS IC is a shared-factor artifact, not cross-sectional discrimination. Keep OFF.
- **`net_debt_to_ebitda` gate-threshold fit: NULL** (XS |t|<1). The `~growth` collinearity is
  universe-sensitive (+0.54 largecap trips, +0.19 smallmid doesn't), so even the "duplicates
  growth" rejection isn't stable. No basis to retune the 4.0 prior.
- **`piotroski` (→ `flags.value_trap.piotroski`): significantly *negative* on smallmid**
  (XS −0.072 t=−3.0 @6m) — a **value-regime artifact** (whole basket: value TS-t +5.9 vs
  quality −4.5 / piotroski −5.1 over ~2021–26), NOT a green light. The conditional value_trap
  mechanism still needs a through-cycle test. Keep OFF.
- Note: `accruals` (a LIVE leg, earned at +0.036 t=2.1 on its 195-name broad universe) reads
  −0.04 (not sig.) here — one regime-contaminated window, not grounds to reverse, but its edge
  is fragile across universes. Worth a through-cycle re-check once snapshot history allows.

Evidence-of-record: `docs/superpowers/specs/2026-06-25-xbrl-broad-universe-results.md` (local,
gitignored per the specs/ convention). The `smallmid` universe is bundled so it's re-runnable.

---

## Predictive-signal pipeline — remaining work (2026-06-21)

Five free-data signals were researched, built (PRs #65/#67/#68/#69/#70, all gated OFF),
backtested, and the two validated winners enabled live:

- ✅ **Live now:** `residual_momentum` (momentum leg, XS rank-IC +0.023 t=2.6 — #72) and
  `accruals` (quality leg, XS rank-IC +0.036 t=2.1 broad — #71).
- ⏸ **Measured & parked:** `asset_growth` (no cross-sectional edge, XS≈0) and
  `shareholder_yield` (strong time-series IC, XS≈0) — both wired + measured, kept OFF.
- ⏳ **Unvalidated (blocked on data):** `sue` and `filing_text_change` (Lazy-Prices) — see #1.

### 1. Turn on daily accumulation to unblock SUE + Lazy-Prices  ← do this first

**Why:** SUE (earnings-surprise drift) and the Lazy-Prices filing-text-change flag cannot be
backtested by the `--source xbrl` or `--source momentum` paths — their inputs (Finnhub
earnings surprises, 10-K/10-Q text) are **not in SEC companyfacts and are not accumulated
anywhere**, so rank IC is currently unmeasurable. Their scoring legs/flag are wired into the
**guarded snapshot-replay** backtest path but **no-op until ≥24 daily `TickerSnapshot`s exist**.

**Action:** enable `shortlist-accumulate` to capture point-in-time snapshots daily.
- A **disabled** systemd timer sample lives in `deploy/` (scheduling ships OFF by design).
  Operator can wire the systemd timer **or** a plain cron entry.
- **Breadth is now ready (#82, 2026-06-26):** the snapshot-replay path needs ≥30 names/date
  (`engine._TRUST_MIN_BREADTH`), so the bundled watchlist is now **42** names and the `deploy/`
  sample sets `--max-tickers 42` (FMP 429s past ~19 names, but the overflow still saves on
  keyless coverage). The library `--max-tickers` default stays 15 for ad-hoc runs, so a *default*
  `shortlist-accumulate run` truncates to 15 and stays below the floor — the timer/cron must pass 42.
- After ~24+ daily snapshots accumulate, run the snapshot-replay backtest to measure the
  `sue` and `filing_text_change` axes' rank IC + collinearity, then enable (or reject) them
  exactly like the other four — flip the config block only if the IC earns it.
- **Test gate reminder:** the suite needs the edgar extra — run `uv run --extra edgar pytest`
  (bare `pytest` errors on pre-existing edgar tests that import pandas).

**Pointers:** `CLAUDE.md` → "Accumulation"; `HARNESS.md` → "Feeding the snapshot path";
`shortlist.data.accumulate` / CLI `shortlist-accumulate`; `deploy/` (disabled sample).
**Status:** pipeline breadth-ready (#82); remaining = operator enables the timer/cron with
`--max-tickers 42`. Not started — operator action.

### 2. (Optional, low priority) Tune the now-live legs — `--fit` is NOT the right tool

`accruals` (in `quality_score`) and `residual_momentum` (in `momentum_score`) ship as
**unfitted priors** — their `config.yaml: thresholds.*` bands and the momentum weight are
hand-set, not fitted.

**Important caveat:** `shortlist-backtest --fit` fits **only the 4 fundamental *composite-axis*
weights** (`quality, moat, growth, value`), requires `--source xbrl`, and **proposes only
(never writes `config.yaml`)**. It therefore:
- can re-propose the **quality axis weight** now that `quality` includes the accruals leg (marginal);
- does **NOT** tune the `accruals` or `residual_momentum` **threshold bands**;
- does **NOT** touch `momentum` at all (it isn't a fit axis), so `residual_momentum` is
  entirely outside `--fit`'s scope.

So `--fit` is **largely not necessary/applicable here.** If you want it for the quality axis anyway:
`uv run --extra edgar shortlist-backtest --source xbrl --fit --fit-horizon 12 --fit-axes quality,value`
(proposal-only; review before hand-editing `config.yaml`).

The more useful (manual) tuning, if the legs underperform in practice: revisit the
`thresholds.accruals` / `thresholds.residual_momentum` bands and the `momentum` weight against
the measured IC. **Status:** optional — the shipped priors are reasonable; only pursue if the
live legs misbehave.

### 3. Harden autobuild before reusing it on this repo

The autobuild run used to build these signals **leaked a spawned session's commit onto local
`main`** (it worked in the live checkout, not its worktree). Recovered; pivoted to manual
isolated-worktree subagents. Before reusing autobuild here, harden it (a hand-off prompt was
drafted) — or at minimum run it against a **throwaway clone**, never the live working repo.
See the `autobuild-signals-backlog` session memory for the full diagnosis.
**Status:** separate repo (`/home/chris/autobuild`); not blocking shortlist work.
