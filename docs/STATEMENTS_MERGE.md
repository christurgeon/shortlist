# Statements merge — year-joined backfill (design)

**Status:** DESIGN APPROVED 2026-07-30. Fixes the 2026-07-20 data-audit item 1
("FMP-won statements silently drop every EDGAR-only field").

Tracked deliberately: `docs/superpowers/specs/` is gitignored in this repo and has
already eaten two design artifacts.

## 1. The defect

`data/models.py:merge_snapshots` routes `statements` through `_pick_first` — a
**whole-source, winner-takes-all** merge — while `harness_sources` ranks `fmp` above
`edgar`. So for every symbol FMP does *not* 402-gate, FMP wins the entire `Statements`
object and every field only EDGAR populates is discarded:

| Field | FMP (`sources/fmp.py:113`) | EDGAR (`sources/edgar.py:193`) |
|---|---|---|
| `diluted_eps`, `diluted_shares` | — | ✅ |
| `fiscal_period_end`, `total_assets` | — | ✅ |
| `asset_growth`, `accruals` | — | ✅ |
| `dividends_paid`, `repurchases`, `debt_repayments`, `debt_issuance` | — | ✅ |
| `gross_profit`, `total_equity` | ✅ | — |

The data is fetched and thrown away: `EdgarSource` runs `get_financials()` for these
names regardless (it "roughly doubles per-ticker EDGAR requests" per CLAUDE.md), and the
merge discards the result.

**Live consequence, traced end to end.** `bridge.py:161` derives `share_count_cagr` from
`st.diluted_shares`; `scoring.py:673` gates the `dilution` advisory flag on it. The flag
ships **ON** (`flags.dilution.min_share_cagr: 0.03`). So the dilution flag is structurally
incapable of firing on exactly the best-covered names, and alive only on FMP-402-gated
ones — the inverse of the intended behavior.

**Compounding cost.** `shortlist-accumulate` persists these snapshots nightly. Every day
the defect stands is a day of §3/§5 measurement inputs written degraded into the store,
and there is no retroactive repair (§6).

**Prior drift.** `sources/edgar.py:216` already carries the comment *"gross_profit/
total_equity aren't in EdgarFinancials; the merge layer fills them from FMP when
available."* The merge layer never did. This design makes that comment true.

## 2. Scope

**Merge layer only.** No FMP-side extraction of the equivalent columns from the
already-fetched income/balance/cashflow payloads. Rationale: pure recovery of data the
harness already has needs no live `/stable/` field-name verification (the repo rule), adds
no quota cost, and keeps the diff narrow. Consequence, stated up front: with `edgar` absent
from `harness_sources`, nothing is recovered.

Out of scope: no new dataclass field, no new fetch, no config block, no scoring leg.

## 3. The merge contract

`statements` moves from `_pick_first` to a new bespoke `_merge_statements`, on the
`_merge_insider` precedent. `_FLAT` is unchanged; `insider` keeps its own merger; the aux
sections keep `_pick_first`.

The merger is **general and priority-ordered**, not FMP/EDGAR-specific — it survives a
`harness_sources` reorder or a future statements source.

**Spine.** The highest-priority source with data (`_has_data`) wins the object outright,
exactly as today. Its `fiscal_years` is the join key. This is why `revenue`/`gross_profit`/
`free_cash_flow` and the 5-year history are untouched, and the growth legs
(`revenue_cagr`, `fcf_cagr`, `revenue_growth_persistence`) cannot change.

**List fields.** Any list the spine left empty is filled from the next source in priority
order that has it, **re-indexed onto the spine's `fiscal_years`** — `None` where the donor
has no row for that year. Emptiness is `_is_present` (`None`, `[]`, `""`), reused as-is: no
new convention.

```
spine.fiscal_years   = [2025, 2024, 2023, 2022, 2021]
donor.fiscal_years   = [2025, 2024, 2023]
donor.diluted_shares = [15.1e9, 15.4e9, 15.8e9]

merged.diluted_shares = [15.1e9, 15.4e9, 15.8e9, None, None]
```

Joining by **year, never by position**, is load-bearing: every consumer of `Statements`
aligns by list index — `piotroski_f` takes five parallel series, `_financial_series`
(`bridge.py:61`) zips ten columns by index, `cagr()` walks each series, the leverage block
reads `[0]` as "latest". FMP typically carries 5 fiscal years and EDGAR ~3, so a positional
backfill would pair FMP's 2022 revenue with EDGAR's 2023 share count — silently, with no
test failing. Both sides derive `fiscal_years` as `int(date_string[:4])` (the calendar year
of the period end), so the key is well-defined.

**Scalars.** `asset_growth`, `accruals`, `dividends_paid`, `repurchases`,
`debt_repayments`, `debt_issuance` — an explicit named tuple, mirroring
`_INSIDER_TXN_FIELDS` — are copied **only when the donor's newest fiscal year equals the
spine's**. These are pre-computed at extraction (the source aligns NI/CFO/Assets by its own
statement dates because the bridge can't), so they carry no positional risk, but a
latest-FY scalar attached to a newer spine would read as current in `--json`/CSV with
nothing marking it as a different vintage. Abstaining follows the repo's
abstain-rather-than-mis-attribute pattern (`entity_match`, `stake.py`, `cusip_map`).

**Abstentions** — never a guess. Two scopes, deliberately different:
- *Whole-merge*: the spine has no `fiscal_years`, or its `fiscal_years` contains
  duplicates. No join key exists, so no backfill happens at all and the result is
  byte-identical to today's `_pick_first`.
- *Per-donor*: a donor with no `fiscal_years`, or with duplicate `fiscal_years`, is skipped
  entirely — the next donor in priority order is still consulted. One unusable source must
  not veto a usable one.
- A donor whose years don't intersect the spine's contributes only `None` cells, which is
  the same as contributing nothing; the field is left empty rather than filled with an
  all-`None` list, so `_is_present` still reads it as absent downstream.

`fiscal_years` itself is never backfilled — it *is* the join key, and the spine defines it.

**Provenance.** `snap.provenance["statements"]` becomes every contributing source in
priority order, as `_merge_flat` does. Today it is a single name, which would misreport a
composed object.

**Copy, never alias.** `_pick_first` returns the winning object *by identity*
(`return obj`), so today's merged snapshot aliases `SourceResult.partial`. A backfilling
merger must `dataclasses.replace()` a copy first, or it mutates the source result in place.
Pinned by a test.

## 4. Blast radius

Every consumer of a recovered field, checked against `config.yaml`:

| Recovered field | Consumer | Status |
|---|---|---|
| `diluted_shares` → `share_count_cagr` | **`flags.dilution`** | **ON — the fix** |
| `diluted_shares` → scored leg | `quality.dilution` | commented out, inert |
| `diluted_eps` → `eps_cagr_ps` | `quality.dilution` | commented out, inert |
| `asset_growth` / `accruals` | `quality.earnings_quality` | both legs `false`, inert |
| financing legs → `shareholder_yield` | `value.shareholder_yield` | commented out, inert |
| `total_assets`, `fiscal_period_end` | `--json`, `financial_series` | display / research context |

**Exactly one live scoring-surface change:** the `dilution` advisory flag becomes able to
fire on FMP-covered names. That is the defect being fixed, not a side effect, and it will
visibly change the daily digest. It is pinned by a regression test rather than hidden
behind a config switch — config-gating is this repo's convention for new *signals*, and
applying it to a merge-layer bug fix would make the broken behavior permanently reachable.

`piotroski_f` is unaffected: all five of its inputs are FMP-supplied already.

## 5. Testing

Unit (`tests/test_statements_merge.py`, sibling to `test_insider_merge.py`):
- ragged spines (5y vs 3y) — values land on matching years, `None` padding elsewhere;
- donor newer than the spine — only overlapping years fill;
- duplicate fiscal year on either side — field abstains;
- spine without `fiscal_years` — no backfill;
- scalar vintage guard, both directions (newest years equal → copied; unequal → `None`);
- source `SourceResult.partial` objects are not mutated;
- reverse direction: EDGAR wins → FMP backfills `gross_profit`/`total_equity`, making the
  `edgar.py:216` comment true;
- provenance lists both contributors in priority order.

Invariance: a single-source snapshot (FMP only, or EDGAR only) produces a `Statements`
byte-identical to `main`.

Regression: an FMP-won + EDGAR fixture whose `diluted_shares` imply
`share_count_cagr ≥ 0.03` now raises the `dilution` flag, where on `main` it cannot.

Live verification (no claim without a run): `uv run shortlist --json` on a non-402 name,
showing `share_count_cagr` / `asset_growth` / `accruals` / `total_assets` non-null where
`main` returns null.

## 6. Known limits

- **Already-persisted accumulation snapshots stay degraded.** There is no retroactive
  repair; the store is complete only from the deploy date forward. This is the argument for
  landing it sooner rather than batching it.
- **No recovery without EDGAR in the chain** (§2).
- **Reverse-split blindness is unchanged** — `share_count_cagr` reads as-reported diluted
  counts with no split guard, exactly as documented in CLAUDE.md. Recovering the field for
  more names widens that exposure without changing its nature.
- The fix does not touch `scoring.score()`.

## 7. Deployment

Standard flow — the live scout/bot run from `/opt/shortlist`, so nothing changes in
production until `git pull` + `sudo bash deploy/install_opt_shortlist.sh`. The accumulate
timer starts capturing the recovered fields on its next run after that.
