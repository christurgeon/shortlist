# EDGAR root cause B — recover `diluted_shares` from the companyconcept API

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Recover `diluted_shares` for 8 of the 9 issuers whose share-count concept is absent from edgartools' income-statement view, by falling back to SEC's single-tag `companyconcept` API. Production coverage **33/42 → 41/42** (XOM cannot be recovered).

## [R2] Size this honestly: HYGIENE / PATH PARITY, not an edge improvement

Plan review measured the live effect and it is smaller than revision 1 implied. **State this up
front so nobody mistakes it for #156-class work.**

All eight recovered series are *shrinking* share counts (2y `share_count_cagr`): CMCSA −5.4%,
CVX −0.6%, GOOGL −1.9%, HON −1.9%, LMT −3.6%, MO −2.7%, MRK −0.8%, PG −0.6% (rounded to 1dp;
the live-measured, more precise figures are in
`docs/audits/2026-08-02-edgar-companyconcept-fallback.md`'s `flags.dilution` table, e.g. CMCSA
−5.440%).
`flags.dilution.min_share_cagr` is **+0.03** (`config.yaml:177`) — **none is within 6 pp of
tripping** — and `quality.dilution` is commented out (`config.yaml:262-264`) so
`scoring.py:498-499` never reads the field. `pe_ttm`/`pe_median_5y` read `diluted_eps` only
(`bridge.py:240-249`), untouched.

**Net effect: a JSON/CSV field goes `null` → number for 8 tickers. No score, gate, ranking or
selection changes. On the 42 measured tickers, no flag changes either — all 8 recovered CAGRs
are negative.** That is a UNIVERSE-scoped claim, not a population-scoped one: outside the 42
(bot discovery, `/screen`, `/portfolio` — all through `EdgarSource`) the ON-by-default
advisory `dilution` flag becomes newly **evaluable** for names that previously abstained
(`flags.dilution`, `min_share_cagr: 0.03`); it stays advisory-only and never affects
`passed`/`composite`/`scored`. One more widening, live in the code: `_edgar_facts.py`'s
`fiscal_period_end=[d for d, _ in (inc_fy or cf_fy)]` means the fallback can fire even for an
issuer with NO income statement at all (cash-flow FY columns only) — the join is still by
explicit `end` date so values stay correct, but that class was never in the measured 42.
#156 by contrast was correcting live corruption (JNJ's sign-flipped `eps_cagr_ps`, MCD's
`pe_ttm = 2.25e-05`).

**The real justification is path parity.** `_xbrl_facts.py:132` already reads
`WeightedAverageNumberOfDilutedSharesOutstanding` from companyfacts, and companyconcept is a
slice of the same data — so **the backtest panel already covered all 8**. The residual-9 skew
therefore never biased the `share_count` *measurement* axis; it biased only the production
harness. Revision 1's "unlock `quality.dilution`" framing was **wrong**: closing B does not
de-bias the evidence for that leg, it merely lets the harness act on a verdict the panel could
already produce — and the largecap XBRL run already measured that axis weak. **This is not a
step toward enabling `quality.dilution`.**

Against `CLAUDE.md`'s design premise: it adds no leg, no flag, no config block, and leaves
`scoring.py` untouched — it improves *what feeds* the funnel on the path that supplies 100% of
production statements. That clears the bar, at one pure function + one seam + ≤9 requests/run.

**Architecture:** A pure aggregator in `providers/_edgar_facts.py` plus one mockable network seam on `EdgarSource`, invoked **only** when statement-level extraction already yielded `[]`. Date-keyed join onto the existing `fiscal_period_end` spine — never positional.

## Evidence — all measured live 2026-08-02, nothing assumed

Root cause B is the 9 tickers left by `#156`: CMCSA CVX GOOGL HON LMT MO MRK PG XOM — their
income statement carries only `EarningsPerShareBasic`/`Diluted`, no share-count tag at any label.

**Route-1 coverage probe** (`companyconcept/.../us-gaap/WeightedAverageNumberOfDilutedSharesOutstanding`):

| ticker | present? | latest 3 annual values |
|---|---|---|
| CMCSA | ✅ | 3.709B / 3.908B / 4.148B |
| CVX | ✅ | 1.856B / 1.817B / 1.880B |
| GOOGL | ✅ | 12.230B / 12.447B / 12.722B |
| HON | ✅ | 0.643B / 0.655B / 0.668B |
| LMT | ✅ | 233.5M / 0.239B / 0.251B |
| MO | ✅ | 1.683B / 1.718B / 1.777B |
| MRK | ✅ | 2.507B / 2.541B / 2.547B |
| PG | ✅ | 2.454B / 2.472B / 2.484B |
| **XOM** | ❌ | **genuinely absent — see below** |

Every value sanity-checks against the real share count (GOOGL ~12.2B, LMT ~234M, PG ~2.45B).

**XOM is a true absence, verified exhaustively, and must ABSTAIN.** Exxon last tagged
`WeightedAverageNumberOfDilutedSharesOutstanding` in **FY2013** (31 entries, newest
2013-12-31). Enumerating *every* us-gaap tag with `unit=shares` carrying a 2024/2025 10-K
value returns 11 tags, of which the only weighted-average one is
**`WeightedAverageNumberOfSharesOutstandingBasic` (4,305,000,000)**. **Do NOT substitute the
basic count** — it is a different measure, and conflating them silently corrupts
`share_count_cagr`. XOM stays uncovered; that is the correct outcome.

**Cost — this is the finding that reverses the plan's original deferral.**
`docs/PLAN_EDGAR_DILUTED_SHARES.md` deferred route 1 partly on cost ("a per-ticker fetch
(~2.5 MB/CIK cached), which the harness deliberately avoids on the hot path"). That was
measured against the wrong endpoint. Measured:

| endpoint | PG | HON | ratio |
|---|---|---|---|
| `companyconcept` (1 tag) | **35 KB** | **32 KB** | — |
| `companyfacts` (all tags) | 3.81 MB | 4.56 MB | **108–140× larger** |

At ~35 KB, this is comparable to any other EDGAR request the harness already makes, and it
fires for at most 9 tickers. The cost objection does not survive measurement.

**Join key validated, not assumed.** HON's companyconcept 10-K `end` dates are
`2025-12-31, 2024-12-31, 2023-12-31` → exactly our `ef.fiscal_period_end`
`['2025-12-31','2024-12-31','2023-12-31']`, where `ef.diluted_shares == []`. A date-keyed
join lands 1:1.

## Landmines found while probing — each needs a guard

1. **CIK resolution via `company_tickers.json` is unsafe for this.** It maps **XOM → 2115436
   ("ExxonMobil Holdings Corp")**, a fee-filing shell whose entire companyfacts payload is
   **1,061 bytes** (`ffd` fee facts from a POSASR). The operating company is **34088**. Using
   that map would silently query the wrong entity. **[R2] But the trap is the RAW MAP, not
   lookup in general:** `Company("XOM").cik` → **34088**, verified live — edgartools does not
   use that first-occurrence row. The source does **not** hold a reusable `Company` handle
   (`_fetch_financials_object` constructs and discards one), so the seam resolves
   `Company(ticker).cik` itself, exactly as `_fetch_sic` already does. See Task 2 §C2.
2. **The same `end` appears multiple times across filings** (restatements / comparatives): HON's
   `2024-12-31` appears under both `fy=2025` (filed 2026-02-17) and its original filing. **Dedup
   by `end`, preferring the most recent `filed`.**
3. **A 10-K payload is not all-annual.** Entries carry `start`/`end`; filter to durations of
   ~1 year (350–380 days) so a quarterly fact can never be read as a fiscal year.
4. **Units are absolute shares** (HON `642,800,000`), whereas the statement path is sometimes
   filer-scaled (MCD `716.4` = millions). `share_count_cagr` is scale-invariant so the scored
   surface is safe, but `financial_series` display will mix conventions across tickers.
   Document it; do not "normalise" by guessing.

## Global Constraints

- **Fallback only.** Fire **only** when `ef.diluted_shares == []` **and** `ef.fiscal_period_end`
  is non-empty. It must be impossible for this path to override a working extraction.
- **Abstain over guess.** Return `[]` unless every spine year is covered — matching `_series`'
  all-or-nothing contract. Never substitute basic shares.
- **Join by date, never position.**
- **Pure/IO split**, the repo's leaf pattern: the aggregator is pure and lives in
  `_edgar_facts.py`; the fetch is a mockable seam on `EdgarSource` beside `_fetch_sic`.
- **Failure-isolated + never-raises.** Any error → `[]`, statements otherwise unaffected.
  This is a *recovery* path; it must never be able to reduce coverage.
- Do not edit `scoring.py` or `bridge.py`. No new dependency. No new config block.
- CI: `uv run ruff check src tests` then `uv run pytest -q` (currently 2236 passed).

## Blast radius — declared up front

`diluted_shares` populates for **8 tickers** (CMCSA CVX GOOGL HON LMT MO MRK PG):

| surface | effect |
|---|---|
| `share_count_cagr` → **`dilution` flag** (ON) | newly computable for 8 names |
| `quality.dilution` scored leg | OFF — inert |
| `financial_series` (research QUANT CONTEXT) | gains a `shrs` column for 8 names |
| JSON/CSV `share_count_cagr` | populated |
| `pe_ttm` / `pe_median_5y` / `pe_vs_history` | **unaffected** — those read `diluted_eps`, which this does not touch |
| `confidence` / `scored` | **unaffected** — no component changes presence |
| **[R2] `_merge_statements` donor path** (`models.py:568-586`) | `_is_present` reads `[]` as absent, so EDGAR's empty `diluted_shares` is never backfilled today. Once populated, on any day FMP wins the spine EDGAR becomes a donor and the field is year-re-indexed onto FMP's 5-year spine → **a series with `None` holes**. `cagr` drops `None`s (`stats.py:55`) so it is safe, but the field's shape changes from "never present" to "present with holes" on that path. Rare (FMP won 1 of 24 store dates) — and rare paths are how the last four blast radii went wrong. |
| **[R2] Accumulation-store discontinuity** | From deploy day, `store.py` snapshots for these 8 carry `diluted_shares`; the ~24 prior dates do not. A future snapshot-replay backtest sees a **mid-panel field-presence break concentrated in 8 large caps** — exactly the non-random presence change that biases a walk-forward fit. Not a bug; **record the deploy date in the audit doc** so a future evaluator can see the seam. |
| **[R2] Cached research briefs** | `_financial_series` (`bridge.py:61-84`) gains a `shrs` cell, rendered by `assess.py:307-308`, but briefs are accession-cached and keep the old table until `--refresh`. |

**Verify at implementation time** whether any of the 8 crosses `flags.dilution.min_share_cagr`
(0.03). From the probe values none is close (all are shrinking share counts — buybacks), so no
new flag should fire; confirm and record rather than assume.

**Unlock:** `#156` recorded "do not enable `quality.dilution` until B is closed" because the
residual skewed non-randomly to old-line industrials/energy/pharma. This shrinks the residual
from 9 to 1 (XOM), largely removing that objection — but enabling that leg remains a separate,
evidence-gated decision, not part of this change.

---

### Task 1: Pure aggregator

**Files:** modify `src/shortlist/providers/_edgar_facts.py`; create `tests/test_edgar_companyconcept.py`

**Produces:** `diluted_shares_from_concept(payload: dict, fiscal_period_end: list[str]) -> list[float]`

- [ ] **Step 1: Write the failing tests** covering, with fixtures shaped like the real payload
      (`{"units": {"shares": [{"start","end","val","form","fy","fp","filed","accn"}, ...]}}`):
  - happy path: three 10-K annual entries matching a 3-year spine → values in spine order;
  - **restatement dedup**: same `end` twice with different `filed` → the later `filed` wins;
  - **duration guard**: a quarterly entry (`start`/`end` ~90d) with an `end` matching a spine
    year is ignored;
  - **partial coverage abstains**: only 2 of 3 spine years present → `[]`;
  - non-10-K forms ignored; empty/malformed payload → `[]`; missing `units.shares` → `[]`;
  - a spine year absent from the payload → `[]` (never a `None` hole, unlike the merge path —
    `_series`' contract here is all-or-nothing).

- [ ] **Step 2:** run, confirm they fail on `ImportError`.

- [ ] **Step 3: Implement.**

```python
# Measured across all 8 issuers' 10-K rows (2026-08-02): observed durations are exactly
# {364, 365}. The 350-380 band also admits 52-week (363) and 53-week (370) filers, so a
# COST-style retail calendar passes if it ever reaches this path.
_ANNUAL_MIN_DAYS, _ANNUAL_MAX_DAYS = 350, 380


def diluted_shares_from_concept(payload: dict, fiscal_period_end: list[str]) -> list[float]:
    """Weighted-average diluted share counts from an SEC `companyconcept` payload,
    re-indexed onto `fiscal_period_end`. Fallback ONLY — the statement view is
    authoritative when it has the row.

    Guards, each from a live-probed failure mode (docs/PLAN_EDGAR_ROOT_CAUSE_B.md):
      - ANNUAL only: a 10-K payload also carries quarterly durations, so a fact is
        used only when end-start is ~1 year.
      - RESTATEMENTS: the same `end` recurs across filings with different values;
        the most recently `filed` wins.
      - ALL-OR-NOTHING: any spine year without a fact -> [] (never a partial series,
        matching _series' contract, so `cagr` can't span a hole). NOTE the cost: an extra or
        partial `inc_fy` column (the "MSFT FY2026" hazard the prior plan named) silently
        yields []. Verified 3 columns for HON/PG/GOOGL/CMCSA and the audit's 42-row table
        shows 3 everywhere, so risk is low — but this is the likeliest partial failure of
        go/no-go clause 1.
      - form == "10-K" ONLY: these payloads carry 8-K recast rows (measured: CMCSA 3, HON 3,
        PG 12) whose `filed` can POSTDATE the 10-K and would win the dedup. It also drops
        10-K/A (LMT 3, PG 3; values identical today). Both drops are deliberate.
      - `filed` is present on 100% of rows across all 8 payloads and is ISO YYYY-MM-DD, so
        lexicographic ordering == chronological (measured 2026-08-02).
    Never raises: malformed input yields []."""
    try:
        rows = ((payload or {}).get("units") or {}).get("shares") or []
    except AttributeError:
        return []
    best: dict[str, tuple[str, float]] = {}
    for r in rows:
        try:
            if r.get("form") != "10-K":
                continue
            start, end = r.get("start"), r.get("end")
            if not start or not end:
                continue
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days
            if not (_ANNUAL_MIN_DAYS <= days <= _ANNUAL_MAX_DAYS):
                continue
            filed = str(r.get("filed") or "")
            val = float(r["val"])
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        prev = best.get(end)
        if prev is None or filed >= prev[0]:
            best[end] = (filed, val)
    if not fiscal_period_end:
        return []
    out = [best.get(e) for e in fiscal_period_end]
    if any(v is None for v in out):
        return []
    return [v[1] for v in out]
```

**Import note (verified 2026-08-02):** `_edgar_facts.py` does **not** currently import
`datetime` — `from datetime import date` must be added. Do not assume it is present.

- [ ] **Step 4:** run tests → pass. **Step 5:** ruff + full suite. **Step 6:** commit
      `feat(edgar): pure companyconcept aggregator for diluted share counts`.

---

### Task 2: Wire the fallback into `EdgarSource`

**Files:** modify `src/shortlist/data/sources/edgar.py`; extend `tests/test_edgar_companyconcept.py`

- [ ] **Step 1: Write the failing tests** using a fake seam (no network):
  - fallback **fires** when `extract_financials` returned `diluted_shares == []` → merged
    `Statements.diluted_shares` is populated;
  - fallback **does NOT fire** when extraction already produced values (assert the seam is
    never called — raise from the double so a call fails the test);
  - fallback **does not fire** when `fiscal_period_end` is empty;
  - seam raising → `diluted_shares` stays `[]`, and the rest of `Statements` is untouched
    (failure isolation);
  - the CIK passed to the seam comes from the edgartools `Company` object, not a ticker map.

- [ ] **Step 2:** run, confirm failures. **Step 3: Implement.**

Add a network seam beside `_fetch_sic` (same docstring convention, "Network seam (mockable)"):

```python
    def _fetch_diluted_shares_concept(self, cik: int) -> dict:
        """Network seam (mockable): SEC companyconcept for the weighted-average
        diluted share count. ~35 KB (vs ~4 MB for companyfacts — measured), fired
        only when the statement view lacks the row. Never raises; {} on any error."""
```

**[R2] C2 — the seam takes the TICKER and resolves the CIK itself.** Revision 1 proposed
reading the CIK off `fin`, else changing `_fetch_financials_object`'s return type. Both are
wrong, probed live:
- `Financials` exposes **no** `.cik`/`.company`/`._company`/`.entity` — `vars(f) == {'xb': …}`.
  The only route is `fin.xb.entity_info["identifier"]`, an undocumented nested internal in the
  exact library layer that silently broke the accruals leg — and its sibling
  `entity_info["ticker"]` returns garbage (`'XOM39A'`). Do not build on it.
- Changing the return type breaks three existing seams: `tests/test_edgar_events.py:181`
  (subclass override), `tests/test_harness_sic.py:40` and
  `tests/test_edgar_source_financials.py:80` (both `monkeypatch.setattr`). Avoidable.
- **`Company("XOM").cik` → 34088**, verified live: edgartools does **not** use
  `company_tickers.json`'s first-occurrence row. So "a fresh lookup" is NOT the fee-shell trap
  — reading the **raw ticker map** yourself is. Revision 1's prohibition was over-broad.

So mirror `_fetch_sic` (`edgar.py:220-228`) exactly: `_fetch_diluted_shares_concept(self,
ticker: str) -> dict` resolves `Company(ticker).cik` internally. No signature changes, no test
breakage, documented public attribute.

**[R2] I1 — do NOT acquire the semaphore inside the seam.** `_edgar_semaphore()`
(`edgar.py:38-42`) calls `asyncio.get_running_loop()`, and the seam runs inside
`asyncio.to_thread` where there is no running loop → `RuntimeError`. `_fetch_sync` already runs
while `fetch()` holds the semaphore (`edgar.py:131-133`), so the seam is transitively bounded.
Use `httpx` with the `SEC_IDENTITY` User-Agent and an **explicit `timeout=`** — nothing else
bounds it, and a hung SEC connection stalls a collector slot.

**[R2] C1 — the fallback MUST be wrapped in its own try/except.** `_build_financials_snapshot`
is called inside `_fetch_sync`'s single try/except (`edgar.py:272-276`); on any exception
`res.partial.statements` is never assigned and the ticker loses **all** statements — revenue,
FCF, leverage, everything. Revision 1's snippet had three paths that could raise (a raising
seam, `int(cik)`, and `AttributeError` escaping the aggregator), which violated this plan's own
"must never reduce coverage" constraint and made its own Task-2 raising-double test
unsatisfiable. Do not rely on the seam's "never raises" docstring as the only guard:

```python
        if not ef.diluted_shares and ef.fiscal_period_end:
            try:                                    # never let a RECOVERY path reduce coverage
                ef.diluted_shares = diluted_shares_from_concept(
                    self._fetch_diluted_shares_concept(ticker), ef.fiscal_period_end)
            except Exception:                       # noqa: BLE001 — fallback is best-effort
                pass
```

**Note (verified 2026-08-02):** `_fetch_financials_object` is
`return Company(ticker).get_financials()` — it **constructs the `Company` and discards it**,
and `_build_financials_snapshot(ticker, fin)` never sees it. So the CIK is not available at the
call site today. Preferred fix, in order:
1. If the edgartools `Financials` object exposes the CIK (probe `fin` for `.cik` /
   `.company` / `._company`), read it from there — no extra request, no signature change.
2. Otherwise change `_fetch_financials_object` to return `(company, financials)` and thread the
   CIK into `_build_financials_snapshot`. Update the existing tests that mock that seam.

**Do NOT re-resolve the ticker to a CIK** via `company_tickers.json` or a fresh lookup — that
is precisely the XOM→2115436 fee-shell trap measured above. Probe option 1 first and record
what you found.

- [ ] **Step 4-6:** tests pass, ruff + full suite, commit
      `feat(edgar): companyconcept fallback for issuers missing the diluted-share row`.

---

### Task 3: Live verification + evidence + docs

- [ ] **Step 1: Live before/after over all 42 store tickers** (keyless; `set -a && . ./.env`).
      Run on `main` and on the branch, diff programmatically.

      **[R2] The script MUST drive `EdgarSource`, not `extract_financials` directly.** The
      obvious script to reuse — the Method block in
      `docs/audits/2026-07-31-edgar-concept-match.md` — calls `extract_financials` directly,
      which **never exercises the new fallback**. It would report zero diffs across all 42,
      reading as "clause 3 passes, clause 1 fails", and burn a full re-run to diagnose. Drive
      `EdgarSource._build_financials_snapshot` (or `_fetch_sync`) and read
      `snapshot.statements.diluted_shares`.

      **Go/no-go:**
      1. The 8 covered tickers go `diluted_shares = []` → 3 real values matching the probe table
         above (CMCSA 3.709B, CVX 1.856B, GOOGL 12.230B, HON 0.643B, LMT 233.5M, MO 1.683B,
         MRK 2.507B, PG 2.4544B).
      2. **XOM stays `[]`.** If XOM gains a value, STOP — something substituted basic shares.
      3. **Every other ticker byte-identical.** Any other change is a STOP.
      4. **Cross-check the recovered values**: `net_income / diluted_eps ≈ diluted_shares` per
         year for the 8. Expect a few percent (consolidated NI vs income to common); flag >5%,
         and an order-of-magnitude miss means a units or wrong-tag problem.
         **[R2] Be honest about what this proves.** A 5% tolerance CANNOT discriminate a diluted
         count from a basic one — they differ by well under 1% for these issuers (the 2026-07-31
         audit says exactly this). It is corroboration, not the guarantee. The real guarantee is
         structural: the seam requests one named concept URL. So **also assert the returned
         payload's own `cik` and `tag` fields echo what was requested** — that is the check the
         arithmetic cannot give.
      5. Record whether any of the 8 crosses `flags.dilution.min_share_cagr` (0.03).

- [ ] **Step 2:** write `docs/audits/2026-08-02-edgar-companyconcept-fallback.md` — the probe
      table, the XOM exhaustive-absence evidence, the 108–140× cost measurement, the before/after
      for all 42, the cross-check table, and repro commands.
- [ ] **Step 3:** `CLAUDE.md` — document the fallback, that it is fallback-only and abstains,
      the CIK-shell landmine, and the mixed units caveat.
- [ ] **Step 4:** `TODO.md` — close item 3; record XOM as the permanent residual and that the
      `quality.dilution` objection is now much narrower (1 ticker, not 9).
- [ ] **Step 5:** commit.

## Done When

- ruff clean; full suite green.
- The 42-ticker go/no-go passes all five clauses, with XOM still `[]`.
- Audit doc committed; CLAUDE.md and TODO.md agree with the code.
- **Not done here:** enabling `quality.dilution` (separate evidence-gated decision); XOM
  (no diluted count exists to recover); deployment.
