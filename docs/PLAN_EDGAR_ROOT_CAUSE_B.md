# EDGAR root cause B — recover `diluted_shares` from the companyconcept API

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Recover `diluted_shares` for 8 of the 9 issuers whose share-count concept is absent from edgartools' income-statement view, by falling back to SEC's single-tag `companyconcept` API. Take production coverage from 34/42 to 42/42 minus XOM (41/42).

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
| LMT | ✅ | 0.234B / 0.239B / 0.251B |
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
   that map would silently query the wrong entity. **Use the CIK from the edgartools `Company`
   object the source already holds** — same resolution the rest of `EdgarSource` uses.
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
        matching _series' contract, so `cagr` can't span a hole).
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
        except (TypeError, ValueError, KeyError):
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

Use `httpx` with the existing `SEC_IDENTITY` User-Agent, bounded by the module's existing
`_EDGAR_MAX_CONCURRENCY` semaphore. In `_build_financials_snapshot`, after `extract_financials`:

```python
        if not ef.diluted_shares and ef.fiscal_period_end:
            cik = getattr(company, "cik", None)   # from the edgartools Company, NOT a ticker map
            if cik:
                ef.diluted_shares = diluted_shares_from_concept(
                    self._fetch_diluted_shares_concept(int(cik)), ef.fiscal_period_end)
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

      **Go/no-go:**
      1. The 8 covered tickers go `diluted_shares = []` → 3 real values matching the probe table
         above (CMCSA 3.709B, CVX 1.856B, GOOGL 12.230B, HON 0.643B, LMT 0.234B, MO 1.683B,
         MRK 2.507B, PG 2.454B).
      2. **XOM stays `[]`.** If XOM gains a value, STOP — something substituted basic shares.
      3. **Every other ticker byte-identical.** Any other change is a STOP.
      4. **Cross-check the recovered values**: `net_income / diluted_eps ≈ diluted_shares` per
         year for the 8. Expect a few percent (consolidated NI vs income to common); flag >5%,
         and an order-of-magnitude miss means a units or wrong-tag problem.
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
