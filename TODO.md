# TODO — follow-ups for a future session

Tracked, low-urgency follow-up work that has no natural home in code comments.
Newest context at top. See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for the signal designs and
`docs/ASSESSMENT_GAPS.md` for the broader scoring roadmap.

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
  Operator can wire the systemd timer **or** a plain cron entry. Cap per run with
  `--max-tickers` (defaults to 15/day for headroom).
- After ~24+ daily snapshots accumulate, run the snapshot-replay backtest to measure the
  `sue` and `filing_text_change` axes' rank IC + collinearity, then enable (or reject) them
  exactly like the other four — flip the config block only if the IC earns it.
- **Test gate reminder:** the suite needs the edgar extra — run `uv run --extra edgar pytest`
  (bare `pytest` errors on pre-existing edgar tests that import pandas).

**Pointers:** `CLAUDE.md` → "Accumulation"; `HARNESS.md` → "Feeding the snapshot path";
`shortlist.data.accumulate` / CLI `shortlist-accumulate`; `deploy/` (disabled sample).
**Status:** not started — operator action (wire the timer/cron).

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
