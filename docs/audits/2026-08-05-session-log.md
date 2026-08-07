# Session log — 2026-08-05/06 discovery-layer work

**START HERE if you are picking this up cold.** Single tracker for the whole workstream: what
shipped, what was measured, what was **retracted**, and what remains (§6). Plan:
`/home/chris/.claude/plans/sunny-shimmying-parasol.md`.

> **SUPERSEDED FOR "WHAT TO DO NEXT" (2026-08-06):** the paid price feed is **not** being
> purchased, so every §6 blocked item stays blocked. The agreed plan under that constraint is
> **`docs/audits/2026-08-06-discovery-breadth-plan.md`**. It also corrects a factual error
> repeated in this log: `edgar_form4` is weight **1.0**, not 1.5 (`config.yaml:736`; PR #152
> lowered it in the same commit that shipped the signal). This log stays authoritative for
> **state** — what shipped, what was measured, what was retracted.

**Trigger:** the daily digest delivered **zero candidates** on 2026-08-04.

**State at hand-off (2026-08-06 ~03:20 UTC):** suite **2400 passing**, ruff clean, working
tree clean, nothing unpushed. **`/opt/shortlist` carries every CODE change** — `main` may sit
a commit or two ahead on docs-only updates (this log), which need no deploy. The **bot was
restarted 2026-08-06 02:57:52 UTC** and runs current code.

Verify on pickup rather than trusting this line:
```bash
git -C /opt/shortlist log --oneline -1     # vs `git log --oneline -1`
uv run ruff check src tests && uv run pytest -q
```

**The two things gating almost everything else:**
1. **Tonight's 22:30 run produces the first `sec_requests`** — the measurement that confirms
   or refutes the Form-4 cascade and gates every Phase 3 originator. Read
   `scout/<date>/manifest.json`.
2. **The paid price feed** (approved, not purchased) gates all cohort measurement.

---

## 1. SHIPPED (merged to `main`, CI green)

| commit | what | deployed to `/opt`? |
|---|---|---|
| `11c6006` (PR #162) | Shared sec.gov throttle; resolver stale-fallback + retry; shared resolver memo; `run_health()` degraded-vs-quiet | ✅ |
| `7ed7eef` | `backtest/prices.py` caches Yahoo 404s (delisted tickers stopped being re-fetched every run) | ❌ not needed |
| `3744612` | Per-consumer SEC budget into `RunManifest.sec_requests`; routed `cik_tickers` + `dera` into the shared throttle; same 404 fix on the **production** Yahoo path | ✅ |
| `46b4cd2` | `cusip_map` strips SEC's `/DE/` state-of-incorporation marker (821 of 10,398 issuers) | ✅ |
| `a38add3` | **Deep-screen quality floor** — `data/secframes.py`, `scout/quality_floor.py`, `funnel.apply_quality_floor`, wiring. Ships **OFF** | ✅ (disabled) |
| `17be9ec` | `dera` reports a code error distinctly instead of as a missing quarter | ✅ |
| `50be4ed` | **Finnhub non-USD market caps abstain** instead of being read as dollars (the $63.9T `TSM` bug) | ✅ |
| `c6406a2`, `1cab195`, `d0c8879`, `7c97fd4`, `c7b8b2f` | Standing-screen audits, adversarial-review corrections, this log | docs |

Suite **2400 passing**, ruff clean.

## 2. Root causes of the empty digest (all fixed)

1. **`wsb_hype`'s demotion** (2026-07-26) silently removed 10–15 of ~16–21 raw candidates/day.
   Correct decision; it exposed how thin the rest was.
2. **One transient SEC 429 bailed every EDGAR originator.** `load_raw_company_tickers` had a
   same-day-only cache key, no retry, no stale fallback. Replaying 08-04 with SEC dead now
   yields **8,000 CIKs** instead of 0.
3. **The Form 4 sweep fetched up to 2,500 filings unthrottled.** Measured latency ~17 ms ⇒ a
   serial loop reaches ~57 req/s, **5.7× SEC's ceiling**. Now one shared ~6 req/s budget.

## 3. Measured findings worth keeping

- **DERA bulk is 127–215 days stale** (its newest published quarter holds filings from 4–7
  months ago) → cannot originate. **SEC `frames` is current** and costs ~12 requests / ~8 MB
  for the whole universe, vs 4,620 requests / 3.8 GB for per-ticker companyfacts.
- **`frames` has no filing date** → LIVE ONLY. Backtests must use DERA, which has `filed` per
  row. Mixing them would import restatement look-ahead into every verdict.
- **DERA `num.txt` (559 MB) streams in 11 s at 32 MB RSS** with stdlib — **no DuckDB needed**.
- **Growth needs no multi-quarter ingestion**: 10-Ks carry comparatives, so one file gives YoY
  for 96.5% of filings and a 2y CAGR for 60.7%.
- **FDIC rejected**: banks-only, so every quiet-day digest would be banks — in the sector
  where the scorer masks the most legs.
- **Decision-surface price coverage is fine** (4 of 5 cohorts ≥0.94); only `8k-neg` (0.88) is
  below the floor.

## 4. RETRACTED / CORRECTED — mistakes made and fixed in-session

Kept deliberately visible; the surviving claims are only worth something because these are.

1. **"The 5 resolver call sites issue redundant fetches"** — wrong; the day-cache means one
   fetch. A test written against it passed *without* the fix. Real defect was narrower
   (intra-run disagreement).
2. **"48% of Form 4 fetches are provably wasted"** — badly wrong. The index `cik` is the
   **filer** (often a natural person), and the ticker comes from `<issuerTradingSymbol>`.
   Sampling showed **80% carried a real ticker**, including SPOT, INCY, SYY. The filter would
   have deleted large-caps.
3. **CLAUDE.md's "every scout SEC consumer draws on the shared throttle"** — written by me the
   same day, and false: `cik_tickers`, `dera` and `efts` were outside it.
4. **"DERA is survivorship-free"** — the *archive* is; my crosswalk used **today's**
   `company_tickers.json`, silently re-excluding delisted filers. Must use the PiT `Symbology`.
5. **"`filed` makes DERA point-in-time"** — necessary, not sufficient: periods reappear as
   comparatives in later ZIPs and can carry restated values.
6. **Rejecting DERA because it is "priced in"** — wrong test for a funnel that is explicitly
   not return-predicting. It fails on *no daily refresh cadence* + a better alternative.
7. **Quality floor v1 dropped `GIPR` (a REIT) and `COE`** — false positives, found by
   measuring against the real ledger. Fixed with the OCF cash-burn condition.
8. **"The binding constraint is the ~10 FMP deep-screen slots/day"** — I wrote this into the
   code, config, CLAUDE.md and two audits. **The nightly digest burns no FMP at all**:
   `daily_push.include_fmp: false` makes `digest_sources` drop FMP from the chain. A slot
   costs a Yahoo/Finnhub/EDGAR screen and a line of the digest. FMP quota binds only the
   bot's `/screen` and `/deep`, which keep the full chain. Corrected in all five places
   2026-08-06. The quality floor's justification survives (a slot is still finite) but must
   not be argued from an FMP number.

## 4b. Day 2 (2026-08-06)

**The 2026-08-05 run recovered**: `raw=13`, **10 names delivered** (vs 0 on 08-04); 13D
resolved 7, Form 4 found 6 from 928 filings. The fixes work in production.

**A mid-run deploy tore the import state.** `git pull` landed at **22:31:27**, 86 seconds
into the 22:30 run: old `SecThrottle` in memory, new `dera.py` from disk → `TypeError`, which
`ensure_quarters` reported as `2026q2 unavailable` — **a bug wearing a data-gap costume**. The
deployed code verifies correct; this was an operational error, now documented in CLAUDE.md
(never deploy 22:30–22:35 UTC; the scout is a oneshot with lazy imports). The masking is fixed:
programming errors now warn distinctly (`17be9ec`).

**`sec_requests` is still unmeasured** — the 08-05 manifest was built by the pre-deploy
`daily.py`. Tonight's run (08-06) produces the first real figure.

**Two filters investigated and NOT built** — the measurement contradicted both:
- *Security-type filter (ETFs/funds).* All 6 non-operating names came from **already-dead**
  originators (`wsb:hype` disabled, `edgar:form4_cluster_buy` retired). 129/135 are operating
  companies. Building it would treat a historical artifact.
- *Foreign-issuer filter.* 23/135 (17%) are 20-F/40-F filers and 15 came from the LIVE
  `edgar:activist_13d` — but they score *better*, not worse: median confidence **0.85 vs 0.76**
  domestic, 83% already gated, and `ASML` (58.0) / `TSM` (67.3) are top scorers. A blanket drop
  would delete the two best foreign candidates.

**A real bug found by following that data (`50be4ed`, deployed).** `TSM` was recorded at
**$63.9 trillion**: Finnhub reports `marketCapitalization` in the issuer's **native currency**
(60,163,096 with `currency: "TWD"`), and the normalizer read it as USD while reading the
currency field two lines above. Since `scoring.py` only trips `below_min_mktcap` when the cap
is *below* the floor, an inflated cap **silently passes** — a 30–1000x overstatement for
weak-currency issuers, quietly favouring the foreign nano-caps the composition audit
complains about. Now abstains unless `currency == "USD"`.

## 4c. Post-deploy verification (2026-08-06)

**Bot restarted** 02:57:52 UTC (PID 3126051). Standing lesson: **`git pull` alone NEVER
updates the bot.** The scout is a fresh process nightly so it self-updates; the bot is
long-running and keeps old code indefinitely. It had been up ~21 h serving `/screen`,
`/deep` and `/portfolio` on pre-fix code.

**The TSM currency fix verified end-to-end** on the deployed code: `market_cap` is now
`None` where it previously held **63,923,289,931,641**. The run was a clean test of the exact
failure path — FMP was 429-limited, so Finnhub was the *only* market-cap source, which is
where the bug used to bite. Note `gates: []` either way: with `market_cap = None` the
`below_min_mktcap` gate cannot evaluate, so the visible outcome is unchanged for TSM. The real
correction lands on **small** foreign issuers, where a 30x inflation previously hid a genuine
microcap.

**`insider: 0.0` investigated — NOT A BUG.** Recorded so nobody re-opens it. TSM's Finnhub
MSPR rows are `[100, -82.42, -97.95, -99.27, 0.11]`; the source takes `mean/100` = **-0.3590**,
the `insider_sentiment` band is `[-0.30, 0.30]`, so it clamps to **0.0** — a real "heavy net
selling" reading, not a coerced `None`. The gate threshold is `-0.60`, and -0.359 > -0.60, so
`gates: []` is correct too. The `flow` leg *did* abstain: `scoring.py:124` requires
`m.market_cap` truthy. `insider = avg([0.0, None]) = 0.0`, and `confidence: 1.0` is right
because the sentiment leg genuinely is present.

**Two hypotheses I raised were WRONG** (kept so they are not re-run):
1. That a `None` was being coerced to 0.0 — the abstain guard already exists and worked.
2. That foreign private issuers are Section-16 exempt (Rule 3a12-3(b)), so TSM's insider data
   could not exist. **TSM has 185 Forms 3/4/5.** It varies by issuer: ASML 0, MANU 14, TSM 185.
   There is no data-quality problem here.

**One genuine observation, NOT acted on.** That single `+100` month swings the equal-weighted
5-month mean by ~+20 points. Without it the mean would be ≈ **-0.70**, which *would* have
tripped `heavy_insider_selling`. So the gate outcome is sensitive to one month and to the
choice of an unweighted mean over Finnhub's window. That is an **unfitted design choice, not a
bug** — changing it moves a live gate and therefore needs measurement, not a hunch.

## 4d. Operational rules learned (read before touching production)

1. **NEVER deploy between 22:30 and 22:35 UTC.** The scout is `Type=oneshot` with **lazy
   imports**, so a mid-run `git pull` leaves a torn state — modules loaded before the pull are
   old, modules imported after are new. Cost us a `TypeError` on 2026-08-05 that surfaced as
   a fake "quarter unavailable". Check `systemctl is-active shortlist-scout.service` first.
2. **`git pull` alone NEVER updates the bot.** The scout self-updates (fresh process nightly);
   `shortlist-bot.service` is long-running and keeps old code indefinitely. It served
   `/screen`, `/deep` and `/portfolio` on pre-fix code for ~21 h. Always
   `sudo systemctl restart shortlist-bot.service` after a deploy that touches
   `data/sources/*`, `screen.py`, `scoring.py` or the report.
3. **Any cohort replay is a Yahoo-load event.** `validate` fetches full price history per
   uncached ticker; ~44 fetches was enough to trip the WAF **IP-wide**, taking down the
   `v8/finance/chart` endpoint production depends on. The old guidance ("don't probe the
   screener") was too narrow. The 404-caching fixes reduce, not remove, this.
4. **The screener is permanently blocked; the chart endpoint is not.** Two different
   mechanisms — a deterministic fingerprint block vs. volume-sensitive throttling. Never say
   "Yahoo is blocked" without naming the endpoint.
5. **`shortlist` takes `--tickers`, not a positional argument.**
6. **Backticks in `git commit -m` get shell-substituted.** Two commit messages were mangled
   this session. Use `git commit -F -` with a heredoc.

## 4e. Open opportunity — is `daily_x: 10` an arbitrary cap?

Falls directly out of retraction #8. Since the nightly digest runs the **free chain**, the
10-name cap is **not** an FMP-quota constraint — it is a config choice. Raising it costs
Yahoo + Finnhub + EDGAR per extra name, and EDGAR is now bounded by the shared ~6 req/s
throttle.

That matters because discovery is thin (raw 3–21/day) and the funnel's real problem is
composition, not volume — but more slots means more of the surfaced names actually get
screened rather than dropped for budget. **`sec_requests` from the next few runs is exactly
the measurement needed to size this**: it says how much SEC headroom a larger `daily_x` would
consume. Do not raise it blind.

## 5. Incidents

- **I tripped Yahoo's WAF** running a cohort replay (~44 price fetches). The chart endpoint —
  which production depends on — 429'd. Corrected guidance: *any* cohort replay is a Yahoo-load
  event, not just screener probes. The 404-caching fixes reduce the recurrence risk.
- Two commit messages were mangled by backticks triggering shell substitution; both amended.

## 6. REMAINING

**Blocked on the paid price feed (approved, not yet purchased):**
- **Phase 0.3 — wire the `form4` backfill cohort.** `edgar_form4` ships **enabled at weight
  1.5** and has *never been measured*: `_BACKFILL_SPECS` has no `form4` row. Needs the
  stateful `assemble_factory` pattern (like `13d-a`) so DERA quarters strictly precede each
  event, plus a look-ahead regression test written first.
- **Phase 0.2 — regime-break audit.** All six preregs use `2022-01-01 → 2025-12-31`, which
  straddles: 13D deadline 10→5 business days (**2024-02-05**), 13G quarterly (**2024-09-30**),
  13D/G structured data (**2024-12-18**), Form 4 10b5-1 checkbox (**2023-04-01**).
- **Attribute the buyback verdict.** The committed KILL (−0.84%/mo, CI entirely negative)
  replayed as **−0.14%/mo, CI spanning zero**. Three variables moved at once; needs a
  like-for-like replay pinned to the original as-of. Not yet a retraction.
- **`8k-neg` coverage/attrition split** (~2,884 fetches; run off-hours).

**Not blocked:**
- ~~Deploy the pending commits~~ — **DONE**; `/opt` and the bot are both current.
- **Enable the quality floor — STILL OFF, deliberately** (proposed in PR #163, then pulled).
  Two reasons, both recorded in `config.yaml` beside the flag: the plan gates it on **≥3
  sessions** of `sec_requests` and only one exists; and its 5.2% evidence is **same-ledger** —
  the GIPR/COE guards were found on the very 135 picks the number is computed from, with no
  held-out set, scored with LIVE-ONLY `frames` rather than a point-in-time replay. Neither is
  fatal; both make the flip a measured decision, not a one-line convenience.
- ~~**Read `sec_requests`**~~ — **FIRST MEASUREMENT DONE (PR #163)**, 2 more sessions needed.
  2026-08-06: `edgar_form4` **741 of 756 = 98.0%**, matching the simulation — the cascade is
  **confirmed**. But `edgar_index_daily_cap` is **NOT** the lever this entry assumed: it has
  never bound (peak 928 = 37% of 2500), and lowering it truncates a *structured* prefix. The
  budget has **13.5× headroom**. See `2026-08-07-funnel-gate-mismatch.md` §2.
- **Phase 1.2 — Form 25/25-NSE** to replace `symbology.py`'s archive.org Wayback dependency
  for delisted tickers (rate-limits datacenter IPs; a single point of failure in the evidence
  base).
- ~~Security-type filter~~ — **THIS CONCLUSION WAS WRONG; FILTER SHIPPED IN PR #163.**
  §4b claimed the affected originators were "already-dead". Both premises were false:
  `edgar:form4_cluster_buy` is the **pre-rewrite emission name of the still-enabled**
  `EdgarForm4Signal` (renamed at the 2026-07-27 rebuild, not retired), and `GLD` came from the
  live `edgar:13f_new_position`. `BBASX`, a mutual fund, was delivered **ungated at composite
  100.0** as a top-ranked pick. Fixed by adding `X` to `_FIFTH_LETTER_SUFFIXES` and applying
  `_junk_suffix` to `edgar_form4`. **`GLD` remains unfixed** — a 3-letter ETF carries no
  suffix marker. Evidence: `2026-08-07-funnel-gate-mismatch.md` §3.
- **Phase 3 originators**, each gated on the SEC budget model: FINRA daily RegSHO (1 req/day),
  SEC comment letters (`UPLOAD`/`CORRESP`), `NT 10-K`, 8-K items 4.01/1.02.

### Recommended next step

**Phase 1.2 — Form 25/25-NSE.** It is the only remaining item whose value is **correctness
rather than a signal awaiting measurement**, so it is worth having regardless of what any
later verdict says. Everything else unblocked (RegSHO, comment letters) would ship disabled
and *stay* disabled until the price feed lands. Caveat: it can be BUILT now, but *validating*
that it does not shift a committed cohort verdict needs the replays, which need prices.

**Explicitly not doing:** a standing full-universe *originator* (adds scoring surface); "build
Lazy Prices" (already exists — `research/textsim.py`, `filings.py:148`); 8-K 4.02 conditioning
(already live in `negative_veto`).
