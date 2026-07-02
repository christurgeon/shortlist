# TODO — follow-ups for a future session

Tracked, low-urgency follow-up work that has no natural home in code comments.
Newest context at top. See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for the signal designs and
`docs/ASSESSMENT_GAPS.md` for the broader scoring roadmap.

---

## Signal-validation harness — Phase 0 + Phase 1 shipped; Phase 2 next (2026-07-02)

The **highest-impact** build (design v3.1: `docs/superpowers/specs/2026-07-01-signal-validation-harness-backfill-design.md`,
local/gitignored; session memory `signal-validation-harness-project`). Turns the ~10 parked discovery
priors into a measurement flywheel.
- **Phase 0 = PR #104 (MERGED + deployed).** Raw-signal firehose (`scout/firehose.py`,
  `ScoutState.firehose`, config-gated, best-effort) + fixed the XBRL look-ahead H1 (nominal
  `quote[0].close` for `market_cap`/`PE`). Firehose logs from the 2026-07-02 nightly run onward.
- **Phase 1 = the evaluator (`feat/scout-validate-evaluator`, PR pending).** `scout/validate.py` +
  `scout/factors.py` (Ken French FF3) + `scout/preregister.py` (git-blob-hash tamper gate, YAML under
  `src/shortlist/scout/preregister/`) + `shortlist-scout validate` CLI + display-only report section.
  CTP → FF3 alpha (manual stdlib OLS) → block bootstrap (block≥K, effective-n = independent blocks) →
  KILL/HOLD/INSUFFICIENT + IR rank, **never PROMOTE**; alpha-uncomputable → INSUFFICIENT. Built TDD via
  subagent-driven dev, whole-branch review READY TO MERGE, 1496 tests pass.

**§14 spikes DONE (2026-07-02, all GO) → Phase 2 GO and improved.** Key reversals: survivorship is
**correctable** via a Wayback CDX resolver of `company_tickers.json` (+ the filing's own
`dei:TradingSymbol` 2019+ as primary); delisting sign **classifiable** (8-K Item 1.03=bankruptcy /
2.01+5.01=M&A); FINRA archive to 2017. Landmines: CIK-reuse (BBBY→Overstock — key on subject CIK,
resolve ticker via as-of snapshot, never ticker→CIK; guard extends to the ticker-keyed Yahoo price
fetch); FINRA rows have NO CIK (need reverse lookup) + go-dark-heavy → **FINRA gated on a measurable-
fraction audit; 13D is the clean first backfill target.**

**Phase 2 build order (spec §16, 5 plans):** (1) `scout/symbology.py` (dei:TradingSymbol+Wayback, fwd
+reverse, reuse `cik_tickers.build_cik_to_ticker`, rate-limited) → (2) `scout/delisting.py` (Form 25/15
+ 8-K classifier, bankruptcy-overrides-M&A precedence, BBBY/ATVI/TWTR fixtures) → (3) `backtest/
edgar_history.py` + 13D backfill leg → (4) FINRA audit spike → (if pass) FINRA leg → (5) wire verdicts
into the daily digest (**at wiring: `dataclasses.asdict()` the SignalVerdicts + normalize the section's
render_text to list[str]** — documented landmine in `report/viewmodel.py`).

**Status:** P0 merged+deployed; P1 complete, PR pending. P2 not started (spikes done; plans in spec §16).

## Congressional-trade copy-trading — evaluated, rejected as scored signal; docs PR pending (2026-07-01)

Branch `docs/congressional-trades-verdict` (off `main`, doc-only) records the verdict:
post-STOCK-Act evidence for copy-trading disclosed congressional trades is null-to-negative
(Eggers-Hainmueller 2013; Belmont-Sacerdote et al. 2020), so it is **rejected as a scored
leg / auto-copy** — contested-prior scout discovery originator at most (cluster buys, FINRA
short-interest pattern), full entry in `PREDICTIVE_SIGNALS_RESEARCH.md` → deferred/rejected.
Also corrects the now-stale "Quiver = highest-leverage add" framing in `DATA_SOURCES.md` C2 +
§2 gap 5, `ASSESSMENT_GAPS.md`, `CLAUDE.md`, `README.md` (gov contracts / lobbying / WSB have
since shipped keyless). If the originator is ever wanted: first a feasibility pass on the free
House Clerk PTR / Senate eFD feeds (PDF/HTML-shaped; community JSON mirrors unmaintained).

**Status:** committed on `docs/congressional-trades-verdict`; push → PR → merge pending.

## Scout delivery-confirmation log — commit/PR + deploy (2026-07-01)

Branch `feat/scout-delivery-log` (off `origin/main`) adds a positive Telegram-delivery log
line in `daily.py` — a successful send now emits `scout: delivered <session> report to
telegram (<n> names)` to stderr (previously **silent** on success; only failures surfaced via
exit-code 2 + a manifest note). Also logs the not-configured journal path and names the failed
transports on partial failure. Test added; 27 related scout tests pass. **Uncommitted.**
Remaining: commit → PR (match #100/#101 flow) → merge → deploy to `/opt/shortlist` (`git pull`
→ `install_opt_shortlist.sh` → restart) so the next 22:30 run logs delivery. Only a *future*-run
fix — can't retroactively confirm the 06-30 push (eyeball Telegram for that).

Also pending: delete the merged branch `docs/todo-fmp-digest-wrapup` (PR #101 MERGED; note is
on `origin/main`) — local `git branch -D` + optional remote delete.

**Status:** code + test done on `feat/scout-delivery-log`, uncommitted; PR/deploy + stale-branch
cleanup pending operator action.

## Verified: first FMP-free digest ran clean (2026-06-30) — item below resolved

The `shortlist-scout` timer fired 2026-06-30 22:30 UTC on `/opt/shortlist` (the deployed repo;
`/home/chris/shortlist` is a stale dev checkout — ignore its `state/`). Confirmed **0 FMP calls**
(`PEG` + `Target upside` null on every name incl. AMD/NKE; all 7 axes still scored, `value`
recovered from EDGAR/Yahoo), the "Free-source screen — /deep for PEG + analyst targets" caveat +
`/deep` block + prior-picks-vs-SPY scoreboard all rendered, `research: false` honored (no Claude
burn), picks recorded (`runs`/`picks` include 06-30), no delivery errors. **Delivery not
positively logged** (the gap the new `feat/scout-delivery-log` branch fixes) — 06-30 push itself
unconfirmed; check Telegram. Paid-FMP flip below remains a deferred decision.

---

## FMP-free daily digest shipped + deployed — verify first run / paid-plan flip (2026-06-30)

`scout.daily_push.include_fmp: false` (#100) makes the unattended digest screen on free
sources (EDGAR/Finnhub/Yahoo/FINRA), reserving FMP's 250/day free quota for interactive
`/deep` (the bot's `/screen`+`/deep` keep the full FMP chain). Merged + deployed to
`/opt/shortlist` and the bot restarted (#99 also raised `research.timeout_s` 600→900 for
heavy filers like WDC). Two follow-ups:
- **Verify the first FMP-free digest run** (`shortlist-scout.timer`, ~22:30 UTC 2026-06-30):
  confirm it spends **0 FMP calls**, still ranks all 7 axes, and the "Free-source screen —
  /deep for PEG + analyst targets" caveat renders. A zero-FMP `AAPL` screen was validated
  pre-ship (all axes scored; only `peg` + `upside_to_target` drop).
- **Paid-FMP flip (deferred decision):** if subscribing to FMP Starter (~$22/mo, 300
  calls/min, no daily cap), set `scout.daily_push.include_fmp: true` (or delete the key) →
  digest uses the identical full chain as the bot, no code change.

**Status:** Done + live. Only the first-run verification and the conditional paid-plan flip remain.

## Daily scout push armed in config — VPS deploy + first run pending (2026-06-29)

`scout.daily_push.enabled` is now `true` (#95; lean digest, `research: false`) — but the flag
alone does nothing until the **VPS** runs it. Remaining operator steps: sync the repo to the box
and enable the `shortlist-scout` systemd timer (`deploy/shortlist-scout.timer`, or
`deploy/install_opt_shortlist.sh`), with `.env` present (`SEC_IDENTITY` ±
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`; without the Telegram pair it journals to `scout/<date>/`
+ stdout and still records picks). Don't read `enabled: true` as "live" — verify the timer on the
box. Until it runs nightly, no picks accumulate, so the selection-ledger forward-return analysis
(item 1 of the next section) stays blocked.

**Status:** config armed (#95); VPS deployment + first nightly run pending (operator action).

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
