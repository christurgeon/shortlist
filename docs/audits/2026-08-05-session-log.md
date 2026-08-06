# Session log — 2026-08-05/06 discovery-layer work

Single tracker for the whole session: what shipped, what was measured, what was **retracted**,
and what remains. Plan: `/home/chris/.claude/plans/sunny-shimmying-parasol.md`.

**Trigger:** the daily digest delivered **zero candidates** on 2026-08-04.

---

## 1. SHIPPED (merged to `main`, CI green)

| commit | what | deployed to `/opt`? |
|---|---|---|
| `11c6006` (PR #162) | Shared sec.gov throttle; resolver stale-fallback + retry; shared resolver memo; `run_health()` degraded-vs-quiet | ✅ |
| `7ed7eef` | `backtest/prices.py` caches Yahoo 404s (delisted tickers stopped being re-fetched every run) | ❌ not needed |
| `3744612` | Per-consumer SEC budget into `RunManifest.sec_requests`; routed `cik_tickers` + `dera` into the shared throttle; same 404 fix on the **production** Yahoo path | ✅ |
| `46b4cd2` | `cusip_map` strips SEC's `/DE/` state-of-incorporation marker (821 of 10,398 issuers) | ❌ pending |
| `c6406a2`, `1cab195`, `d0c8879`, `7c97fd4` | Standing-screen audits + adversarial-review corrections | docs |
| *(this commit)* | **Deep-screen quality floor** — `data/secframes.py`, `scout/quality_floor.py`, `funnel.apply_quality_floor`, wiring. Ships **OFF** | ❌ pending |

Suite **2393 passing**, ruff clean.

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
- **Decide whether to enable the quality floor.** Evidence:
  `2026-08-05-quality-floor-evidence.md`.
- ~~Deploy `46b4cd2` + the quality floor~~ — **DONE**; `/opt` is on `50be4ed`.
- **Enable the quality floor — but NOT before tonight's run.** It adds ~16 `secframes`
  requests, which would contaminate the first `sec_requests` baseline. Enable after reading it.
- **Read `sec_requests`** from the next few manifests. If `edgar_form4`'s share holds near the
  98.7% the simulation suggested, the cascade is confirmed and `edgar_index_daily_cap` is the
  lever.
- **Phase 1.2 — Form 25/25-NSE** to replace `symbology.py`'s archive.org Wayback dependency
  for delisted tickers (rate-limits datacenter IPs; a single point of failure in the evidence
  base).
- ~~Security-type filter~~ — **investigated, not needed** (§4b): the source originators are
  already retired.
- **Phase 3 originators**, each gated on the SEC budget model: FINRA daily RegSHO (1 req/day),
  SEC comment letters (`UPLOAD`/`CORRESP`), `NT 10-K`, 8-K items 4.01/1.02.

**Explicitly not doing:** a standing full-universe *originator* (adds scoring surface); "build
Lazy Prices" (already exists — `research/textsim.py`, `filings.py:148`); 8-K 4.02 conditioning
(already live in `negative_veto`).
