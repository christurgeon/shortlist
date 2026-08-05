# Discovery-funnel audit — why 2026-08-04 delivered zero candidates (2026-08-05)

**What this is:** a diagnosis of the 2026-08-04 empty daily digest, run against the live
`/opt/shortlist` state, the committed run manifests, and the source. Written to answer one
question: *is the discovery layer broken, starved, or correctly reporting a quiet day?*

**Answer: all three at once, from three independent causes.** Two are real defects, one is a
deliberate decision whose side-effect was never accounted for. Underneath them sits a
structural gap: **the scout has no non-event originator**, so a day with no qualifying
filings is *structurally* empty, not incidentally empty.

Committed here (not under the gitignored `docs/superpowers/specs/`) per the CLAUDE.md
"commit the evidence" rule.

**Status: DIAGNOSIS ONLY. Nothing in §6 is implemented.** No code, config, or production
state was changed by this audit.

---

## 1. The funnel arithmetic

`raw` is the exact sum of the per-signal emissions — no losses hide between discovery and
the funnel counter. From the committed `scout/<date>/manifest.json` files:

| session | wsb | 13D | form4 | 13F | yahoo | **raw** | delivered |
|---|---|---|---|---|---|---|---|
| 07-27 | 15 | 4 | 2 | 0 | ✗ WAF | **21** | 10 |
| 07-29 | 10 | 5 | 1 | 0 | ✗ WAF | **16** | 10 |
| 07-30 | *off* | 2 | 1 | 0 | ✗ WAF | **3** | 2 |
| 07-31 | *off* | 3 | 3 | 0 | ✗ WAF | **6** | 5 |
| 08-03 | *off* | ✗ **0** | 3 | 0 | ✗ WAF | **3** | 3 |
| 08-04 | *off* | ✗ **0** | **0** | 0 | ✗ WAF | **0** | **0** |

Every row sums exactly (15+4+2=21, 10+5+1=16, 2+1=3, 3+3=6, 3+0=3, 0+0=0). The raw-signal
firehose corroborates the collapse independently: ~14–34 events/day through July, then
`3, 9, 3, 3` for 08-02..08-05.

**Of twelve configured signals, exactly two were originating candidates by 08-03**, and both
failed on 08-04.

---

## 2. Cause 1 — the WSB demotion removed 60% of the funnel (deliberate, correct, unaccounted)

`wsb_hype` was flipped to `enabled: false` in `7398ef2` (#151), commented *"DEMOTED to
confirmation-only 2026-07-26."* That is an evidence-based kill and this audit does not
dispute it — the 2026-07-26 funnel-composition audit found `wsb:hype` was **60% of all picks
at a $310B median market cap**, i.e. mega-cap chatter where no retail edge exists.

But it was silently carrying **10–15 of the ~16–21 raw candidates/day**. Removing it did not
degrade quality; it *revealed* that the remaining funnel was already threadbare. No
replacement origination was added, and no floor or alert existed to make that visible.

**This is not a regression to revert. It is the reason the other two causes became visible.**

---

## 3. Cause 2 — one transient SEC 429 kills every EDGAR originator for a full session

**Verified.** `.cache/sec_tickers/` on the VPS holds `company_tickers-<date>.json` for every
session through **2026-08-03** and **none for 2026-08-04**.

`cik_tickers.py:load_raw_company_tickers` caches on a **strictly same-day key**
(`company_tickers-{today}.json`), makes **one attempt, with no retry**, and swallows every
exception into `return {}`. `load_cik_to_ticker` then returns `{}`, and `signals.py:96`
bails the signal with `_CIK_RESOLVER_EMPTY_MSG`.

Three separate weaknesses compound:

1. **No stale fallback.** A valid 24-hour-old index was on disk, unread. The ticker map
   changes by a handful of rows per day — yesterday's copy is a near-perfect substitute, and
   far better than abstaining on every row.
2. **No retry.** SEC's own guidance is to back off and retry; a single 429 is treated as
   terminal.
3. **Five independent call sites** (`signals.py:487, 583, 753, 1024`, `symbology.py:215`)
   each load the resolver separately. One upstream failure blanks 13D, 13D/A, 8-K, buyback
   and 13F symbology together — and in the healthy case they can issue redundant fetches.

SEC returned `HTTP 200` (795,660 B) to the exact call the code makes when probed during this
audit, confirming 08-04 was transient. The failure is **not** identity/UA shape — both the
bare-email `SEC_IDENTITY` and a `Name email` form return 200.

**Severity: HIGH.** The single highest-weight originator (`edgar_activist_13d`, weight 1.5)
was dead for two consecutive sessions from a fault with a one-line mitigation.

---

## 4. Cause 3 — the Form 4 rewrite floods sec.gov unthrottled (inferred, strong)

`edgar_index.py:155` loops `f.full_text_submission()` over up to **`max_filings`** filings
with **no throttle, no jitter, and no backoff**:

```python
candidates = _dedup_by_accession(filings)[:max_filings]
for f in candidates:
    out.append(f.full_text_submission())        # one SEC request each, unthrottled
```

`edgar_index_daily_cap` was raised **400 → 2500** by the Form 4 rewrite (#152, deployed
~07-30). SEC fair-access is ~10 req/s. Only `thirteenf.py:31` (`SecThrottle`, 0.34 s
min-interval) throttles at all, and it is **per-signal — there is no shared sec.gov
throttle**, a limitation CLAUDE.md already records for 13F.

**The correlational evidence is strong but this causal link is inferred, not proven:**

- `dera: 2026q2 unavailable: HTTP Error 429: Too Many Requests` appears in the 08-03 **and**
  08-04 runs — and *only* those runs. On 07-30/07-31 the same line reads `HTTP Error 404`
  (a genuinely absent quarter), not 429.
- `company_tickers.json` fetch fails on exactly those two sessions.
- On 08-04 the Form 4 signal self-limited to **46 filings** where 325–366 is its normal
  daily haul — consistent with it being throttled mid-sweep by the load it generated.

**Severity: HIGH.** This is the plausible trigger for Cause 2, which makes the two defects a
single cascade: Form 4 exhausts the SEC budget → the resolver's one un-retried fetch loses →
every EDGAR originator abstains → `raw = 0`.

---

## 5. Structural findings (not defects — design gaps)

### 5a. There is no non-event originator, and there never has been on this box

Every surviving signal requires a *filing to occur*. The only standing universe screen —
`YahooScreenerSignal` — is **WAF-blocked on every logged run without exception**. It has
never contributed a single candidate from this VPS.

So the intuition that "there should always be interesting tickers out there" is right, and
the funnel structurally cannot express it: with no filings, there are no candidates, by
construction. **There is no `min_candidates`, no fallback universe, and no empty-day
handling anywhere in `daily.py`** (verified by grep).

### 5b. `edgar_13f` is dormant by design, not broken

`0 new 13F positions from 0 filings (7 funds)` on every run is **correct**. All seven funds'
May 13F-HRs are in `thirteenf_seen`; the Q2 burst lands at the ~Aug 14 deadline. 13F is
inherently a **four-days-a-year** originator and contributes nothing on ~95% of sessions. It
should never have been counted on for daily flow.

### 5c. The composition problem from 2026-07-26 is unchanged

The surviving originators skew hard to nano-caps. The 08-04 scoreboard is entirely
microcaps — KUST, JBDI, TOP, COSM, XAIR, NWFL, BWMX — matching that audit's finding of a
**$50M median market cap for `edgar:activist_13d`**. Fixing *volume* without fixing
*composition* would just surface more nano-caps. Any new originator should be judged on
whether it lands names in the **$0.3–10B** band, not on how many rows it emits.

### 5d. The report is honest, but the failure is not escalated

The delivered 08-04 `report.txt` *does* name every failure inline (`yahoo_screener ✗ ...`,
`edgar_activist_13d ✗ CIK→ticker resolver empty`) and prints `Funnel: 0 raw → …`. Credit
where due: nothing was hidden. But it reads as one long undifferentiated line, and **a
degraded run and a genuinely quiet day produce the same shaped report**. Nothing pages, and
nothing distinguishes "two originators broke" from "nobody filed."

---

## 6. Candidate remedies (ranked, NOT implemented)

Ordered by (impact × confidence) ÷ effort. Items 1–3 are defect fixes; 4–6 are new surface
and would need the repo's measure-first treatment before shipping enabled.

| # | Change | Addresses | Notes |
|---|---|---|---|
| 1 | **Shared sec.gov throttle** across every SEC consumer (reuse `SecThrottle`) | §4 | Highest impact. Cures the cascade at its source. |
| 2 | **Stale-cache fallback + bounded retry** in `load_raw_company_tickers`; accept the newest cached index up to N days old, labelled | §3 | ~10 lines. Would alone have saved both lost sessions. |
| 3 | **Single shared resolver instance** per run instead of 5 call sites | §3 | Removes redundant fetches and single-point blast radius. |
| 4 | **Retire or replace `YahooScreenerSignal`** — it has a 100% failure rate here | §5a | Keeping a permanently-broken signal enabled trains you to ignore ✗ marks. |
| 5 | **A standing universe screen** as the non-event originator | §5a | The user's stated preference is that the report always surface something. Must be judged on §5c composition, not volume. |
| 6 | **Escalate degraded runs** distinctly from quiet ones | §5d | Cheap; makes §3/§4-class faults self-reporting rather than needing an audit. |

### Keyless endpoints probed live from this VPS (2026-08-05)

Evidence for #4/#5. Probed with a browser-shaped header set:

| endpoint | result |
|---|---|
| `api.nasdaq.com/api/screener/stocks` | **HTTP 200, JSON** — keyless, a direct Yahoo-screener replacement |
| `nasdaqtrader.com/rss.aspx?feed=tradehalts` | **HTTP 200, XML** — keyless halts feed |
| `finviz.com/screener.ashx` | HTTP 200 but **HTML** (~200 KB) — scraping, not an API |
| `stockanalysis.com/api/screener/s/f` | HTTP 404 — no usable public path found |
| `query1.finance.yahoo.com/.../screener/predefined/saved` | **HTTP 429 text/html** — WAF, as always |

None of these are endorsed yet; they are evidence that a keyless standing screen is
*feasible* from this box, nothing more.

> **⚠ OPERATIONAL CAUTION, learned the hard way during this audit.** Probing the Yahoo
> *screener* endpoint by hand tripped Yahoo's WAF **IP-wide**, and the `v8/finance/chart`
> price endpoint — which production depends on — returned `429 text/html` for minutes
> afterward. Production was unaffected (the 08-04 run's `JBDI-2026-08-04.json` is a real
> 56 KB payload, and accumulate captured 32 tickers at 71% mean coverage), but **do not
> hand-probe the Yahoo screener from the VPS.** It can take down the price feed the whole
> scorer runs on. This refines, and does not contradict, the standing note that the chart
> endpoint works here while the screener does not.

---

## 7. What was checked and found healthy

Recorded so a future session does not re-investigate these:

- **`SEC_IDENTITY` is correctly set and accepted** — both UA shapes return 200.
- **Yahoo price/chart works in production** (§6 caution above).
- **The Aug 1–2 no-op runs are correct.** `run for 2026-07-31 already completed; nothing to
  do` is the weekend guard — Aug 1/2 2026 were Saturday/Sunday.
- **`/opt/shortlist` config has no local drift** — `git diff config.yaml` is empty; the
  deployed config is the committed one.
- **The six disabled signals are disabled on evidence**, not by accident: `edgar_8k` and
  `edgar_buyback` were KILLed by pre-registered backfills, `finra_short_interest` and
  `edgar_13d_stake_increase` are measure-first pending their cohorts, `quiver` was never
  built, `wsb_hype` was demoted (§2).
- **`finnhub_news` / `wikipedia` are enrichment, not origination** — they only annotate
  already-discovered tickers (`checked 0 tickers`), so they can never raise `raw`.

---

## 8. Honest limits of this audit

- The Cause-3 → Cause-2 causal link (§4) is **inferred from timing correlation**, not proven.
  Confirming it needs an instrumented run counting sec.gov requests per session. The
  mitigation (#1) is worth doing regardless, since an unthrottled 2500-request sweep is
  wrong on its own terms.
- **Two sessions is a small sample.** The 13D resolver failure may recur at a different rate
  than 2-in-2 suggests.
- No claim is made here about whether any *new* source would carry alpha. Per the repo's
  design premise, a new originator earns its slot through a pre-registered cohort, not
  through this document.
