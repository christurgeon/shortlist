# Standing-screen spike — FDIC BankFind (2026-08-05)

**Purpose (plan Phase 2):** decide, on evidence rather than argument, whether FDIC BankFind
can serve as the scout's **standing non-event originator** — the thing that stops a quiet
filing day from being structurally empty.

**Time-boxed spike. NO production wiring.** Script: scratchpad `fdic_spike.py`. Uses no
Yahoo (WAF-degraded) and no paid data.

**Verdict: feasible and the best composition we have measured — but NOT recommended for
adoption as-is.** §4 is the reason, and it is a design decision, not a technical blocker.

---

## 1. Feasibility on this box — comfortable

| metric | measured |
|---|---|
| FDIC active institutions | **4,254** |
| requests to get the whole universe | **1** (keyless, `limit=10000`) |
| peak RSS | **43 MB** (box has 1.9 GB) |
| wall time | 225 s — but 196 s of that was the spike's own Finnhub cap lookups |

The universe itself is **one keyless request**. That is cheaper than any originator we run.

## 2. Crosswalk — reuses existing code, yields ~196 names

FDIC exposes `NAMEHCR` (holding-company name), `RSSDHCR`, and `LEI`. The cheapest bridge is
`NAMEHCR` → `company_tickers.json` title, which **reuses `cusip_map.normalize_issuer_name` +
`build_name_to_ticker`** unchanged — including their abstain-on-cross-CIK-collision rule.

- 256 institutions matched → **196 distinct listed tickers** (6.0% of institutions; the rest
  are private community banks, correctly unmatched).
- The research's estimate was 200–300, so this is in range but likely under-matching. A
  second cause was identified and is FDIC-specific: **FDIC abbreviates "BANCORP" to
  "BCORP"** (`AMERIS BCORP` vs SEC's `Ameris Bancorp`). An FDIC-side alias would recover
  more; that belongs in an adapter, not in the shared normaliser.

## 3. Composition — the decisive metric, and it is a clear win

The 2026-07-26 funnel-composition audit found the funnel barbelled at nano- and mega-caps,
with almost nothing in the $0.3–10B band where a retail-sized book can be early. Market caps
here come from **Finnhub** (not Yahoo); 195 of 196 resolved.

| band | n | share |
|---|---|---|
| < $0.3B | 45 | 23.1% |
| **$0.3–2B** | **71** | **36.4%** |
| **$2–10B** | **44** | **22.6%** |
| > $10B | 35 | 17.9% |

**59.0% lands in $0.3–10B.** Against the existing originators from the 2026-07-26 audit:

| originator | in-band share |
|---|---|
| `wsb:hype` (killed) | 8% |
| `edgar:activist_13d` | 21% |
| `edgar:13f_new_position` | 33% |
| `edgar:form4_cluster_buy` | 39% |
| **FDIC standing universe** | **59%** |

Roughly **1.5× the best current originator and ~3× the 13D originator.** On the metric the
plan pre-committed to, FDIC wins decisively.

## 4. Why it is NOT recommended as-is — two coupled problems

**(a) Banks-only means every quiet day's digest is banks.** The screen is a *standing*
source, so it fires precisely on the days nothing else does. Its sector concentration is
therefore not diversified away by the event originators — it IS the quiet-day report. A
systematic all-financials digest is a strong, permanent tilt that nobody chose.

**(b) The scorer is structurally weakest on exactly this sector.** `sectors.masked_legs`
abstains, for financials: `gross_margin`, `gross_margin_stability`, `roic`, `fcf_yield`,
`fcf_cagr`, `interest_coverage`, `debt_to_equity`, plus the `negative_fcf`/`over_leveraged`
gates. That means **`moat` abstains ENTIRELY** (all three of its legs are masked) and its
0.18 weight is redistributed; `quality` and `value` each lose legs. CLAUDE.md is explicit
that v1 masks but does **not** recalibrate the survivors: *"Sector-specific recalibration of
the surviving legs is still future work."*

So we would be feeding a high-quality universe into the part of the scorer with the least
discriminating power — and ranking banks on bands fitted for operating companies.

**The obvious answer makes it worse, not better.** FDIC call reports carry the metrics that
*would* discriminate (NIM, NPLs, reserve coverage, CRE concentration, uninsured deposits).
Using them means building a **sector-specific scorer**, which is a far larger commitment and
sits in direct tension with the design premise in CLAUDE.md: *"stop adding scoring legs
hoping one crosses t=2… New scoring legs are the exception, gated hard on reproducible
cross-universe rank IC."*

## 5. What was NOT compared

> **RESOLVED 2026-08-05 — see `docs/audits/2026-08-05-standing-screen-spike-dera.md`.**
> DERA was spiked like-for-like and **wins**: 4,620 listed tickers vs 196, CIK crosswalk at
> 88.3% (no name matching), natively point-in-time, all-sector, 52 MB peak RSS. FDIC keeps
> the higher in-band *percentage* (59.0% vs 50.3%) but DERA yields ~20× more in-band names in
> absolute terms — the percentage gap is an artefact of FDIC's narrowness, exactly as
> suspected below. **FDIC is not adopted, as primary or fallback.**

The plan called for choosing between FDIC, DERA financial-statement data sets, and the
Nasdaq screener **on spike evidence**. Only FDIC was spiked. Given §4, **DERA may well be the
better standing screen despite being heavier** — it is all-sector, so it neither concentrates
the quiet-day digest nor lands entirely inside the scorer's masked region. That comparison is
the honest next step; declaring FDIC the winner on §3 alone would ignore the reason §3 is
easy to win (a narrow, homogeneous universe).

## 6. Byproduct — a live production bug, found and fixed

Diagnosing the crosswalk's miss rate surfaced a real defect in shared code:
`normalize_issuer_name` did not strip SEC's trailing **state-of-incorporation marker**
(`/DE/`, `/MN`, `/NEW`), leaving a stray token that can never match — `TJX DE`,
`WELLS FARGO MN`, `QUALCOMM DE`, `COSTCO WHOLESALE NEW`, `APPLIED MATERIALS DE`, `ARM UK`.

**821 of 10,398 live issuers (7.9%)** were affected. This is the **live** CUSIP→ticker NAME
fallback used by the enabled `edgar_13f` signal. Scope honestly: it is **tier 2 of 3** (SEC
fails-to-deliver files resolve first, name-match second, abstain third), so most of these
issuers still resolve via FTD — the defect weakened the *backstop*, it did not blind 13F to
Costco. Fixed, TDD, with measured impact:

- 824 issuer names corrected.
- Net-new cross-CIK collisions: **4** (`COMMUNITY BANCORP`, `FIRST BANCORP`,
  `INDEPENDENT BANK`, `RESOLUTION MINERALS`) — all genuinely ambiguous, and the existing
  collision guard makes them **abstain**, never a wrong-ticker guess.
- Anchored to the string END only, so an interior slash (`A/B Testing`) is untouched.

**Live-surface note:** more CUSIPs may now resolve by name, so `edgar_13f` can emit names it
previously abstained on. That is data recovery reaching cases the code was written for (the
`statements`-merge precedent), not a new feature — but it is a real change to an enabled
originator and is recorded here rather than left implicit.

## 7. Recommendation

1. **Do not wire FDIC as the standing screen yet.** §4 is a product decision about whether an
   all-banks quiet-day digest is wanted, and about whether bank-specific scoring is on the
   table. Both are the user's call, not an implementation detail.
2. **Spike DERA next** for a like-for-like comparison, measuring the same three things.
3. Keep the §6 fix regardless — it stands on its own.
