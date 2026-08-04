# TECH-DEBT.md burn-down — 1 item fixed, 2 inherited to TODO.md, file retired

**Date:** 2026-08-04 · **Change:** `TECH-DEBT.md` deleted; one latent correctness bug fixed
(`_fmp_insider` three-way classification + abstain guard); two blocked items migrated to
`TODO.md`; three "investigated — do not fix back" verdicts converted into code comments at
the sites they protect.

Committed evidence of record, written because deleting the file would otherwise destroy the
most valuable thing in it: the *negative* results — the documented reasons certain obvious
fixes are wrong. Per CLAUDE.md ("prefer making the guard mechanical over documenting it
harder — a rule can be read past, a suppressed field cannot"), those verdicts now live next
to the code rather than in a file nobody opens.

## What the file actually contained

14 entries. **11 were already closed** — 7 `RESOLVED`, 4 `INVESTIGATED — no-op / do not
pursue`. It was a historical record, not a backlog. Only 3 items were open.

Before acting, all three durable no-op verdicts were re-verified against current `main`
(`94426d6`). **All three still hold exactly as written:**

| Verdict (as recorded) | Re-verified 2026-08-04 |
|---|---|
| `ret_1m`/`ret_3m`/`ret_12m` are dead — set by FMP + mock, read by nothing | **Holds.** Only `models.py:154-157` declarations; no reader anywhere in `src/`. `ret_6m` is likewise write-only (set by `yahoo_prices.py:208`, `fmp.py:148`; never read) |
| `backtest/metrics.py` `else 0.0` bucket branch is unreachable | **Holds.** `len(clean) >= 4` early-returns; the collapse loop exits with `nb == 2` or `len(clean) // nb >= 2`, so `size >= 2` and every bucket spans ≥1 row |
| `backtest/cli.py:_load_histories` serial fetch is by design, not debt | **Holds.** Still serial, SPY-first; the Yahoo-WAF politeness rationale is unchanged |

## Item 3 — FMP insider non-`P` codes (FIXED)

`providers/_fmp_insider.py:is_buy()` returned True only for `P`-prefixed codes and
`net_value()` did `net += v if is_buy else -v` — so awards (`A`), option exercises (`M`),
gifts (`G`), tax-withholding (`F`) and conversions (`C`) all counted as **sells**, both
subtracting from `net_value_6m` and inflating `sell_count`. The sibling EDGAR path
(`_form4.classify_code`) three-ways correctly and skips `other`, so the two insider paths
disagreed, biasing FMP's net flow bearish (a routine option exercise read as selling).

**The trap that kept this deferred, and how it was avoided.** The tempting one-liner — reuse
`_form4.classify_code` on both paths — is wrong on two counts, both re-confirmed here:

1. `classify_code` does an **exact** `c == "P"` because it parses edgartools' bare
   single-letter `Code` column. FMP's `transactionType` is an **enriched string**
   (`"P-Purchase"`, `"S-Sale"` — the `tests/test_fmp_insider.py` fixtures), which is why the
   old `is_buy` used `.startswith("P")`. Feeding `"P-PURCHASE"` to `classify_code` returns
   `"other"` — a naive swap classifies **100% of FMP transactions as non-trades**.
2. That all-`other` result **clobbers EDGAR**. `harness_sources` orders `fmp` before
   `edgar`, and `_merge_insider` takes the coupled txn facts wholesale from the first source
   with a present field. The broken swap builds `Insider(net_value_6m=0, buy_count=0,
   sell_count=0, recent=[])`; `_is_present(0)` is `True`, so that zero record **wins the
   merge and discards EDGAR's authoritative data** — the exact opposite of the intent.

The fix therefore has two halves, matching the approach the deferral note itself prescribed:
`_fmp_insider.classify_tx()` — its own three-way that splits FMP's `<CODE>-<Description>`
form and matches the leading letter, never fed to the exact-match `classify_code`; and a
**`found`-style abstain guard** in `data/sources/fmp.py` (mirroring `_form4.Form4Summary.found`)
so a batch with no real P/S trade leaves `snap.insider` unset rather than zero-clobbering.
`is_buy` survives as a thin back-compat wrapper; the existing `"P-Purchase"` tests were kept
as the real-format contract, not rewritten to pass.

**Scope of effect — honest sizing.** This is **output-neutral today**. FMP's free tier 402s
the insider endpoint, and there is no `FMP_API_KEY` on this host, so the path never fires.
It changes numbers only on a **paid FMP Starter tier**, where FMP returns insider data and
wins the merge — which is exactly the data a free-tier backtest cannot measure, so "backtest
the insider axis first" is close to unsatisfiable here. The change was taken anyway because
the abstain guard is defensive correctness independent of tier, and because the two insider
paths are documented as needing to agree. `buy_count`/`sell_count` remain JSON/display-only
(the scorer plumbs `net_value_6m`/`sentiment_mspr`), and the `heavy_insider_selling` gate
runs off Finnhub MSPR, not this classification — so the blast radius is `net_value_6m` alone.

**Latent question deliberately NOT smuggled in here:** should `fmp` outrank `edgar` for the
insider transaction group at all? CLAUDE.md calls EDGAR "the free authoritative source", yet
enabling FMP Starter would silently override EDGAR's insider numbers. That priority question
deserves its own treatment.

## Items 1 and 2 — inherited to `TODO.md`, both externally blocked

Neither is fixable on this host; full detail in the 2026-08-04 `TODO.md` entry.

- **Momentum Stage 0 prize-bound re-run.** `prize_bound.run_live` is Yahoo-dependent and
  `oracle-prod` is Yahoo-IP-blocked; no `FMP_API_KEY`; an 80-name screen costs ~1040 FMP
  calls against a 250/day free cap. It is a **measurement**, not a code change. The decision
  rule (compare `mom_12_1`'s full-basket τ to 0.947) is carried over verbatim.
- **Finnhub `roiTTM` → `roic`.** The deferral gated this on a quality/moat backtest, but
  **that gate is unsatisfiable as scoped**: `--source xbrl` derives ROIC from SEC
  companyfacts and never exercises the Finnhub fallback path, so no available backtest can
  measure this proxy. Re-scoping is the prerequisite, not another run. The documentation
  half (an inline comment marking it a deliberate proxy) was already done 2026-06-26.

## Knowledge preserved mechanically

Three comments added where a future reader would otherwise re-litigate a settled question:

- `backtest/metrics.py` — the `else 0.0` is unreachable, with the proof inline, so it is not
  "fixed" into an abstention on the theory that empty buckets silently score 0.
- `backtest/cli.py:_load_histories` — SERIAL BY DESIGN, with the Yahoo-WAF rationale and the
  revisit condition (bounded `Semaphore(3-5)`, SPY-first) inline.
- `data/models.py` — the `ret_*` fields are write-only, with the reason they are kept
  (persisted snapshot state) rather than deleted.

The `ret_*` fields were **left in place**. They are genuinely dead, but they are also
serialized `TickerSnapshot` state read back by the accumulation store, so removing them is a
cosmetic change with a real back-compat surface — poor risk/reward, and the original note
had already ruled the item a no-op.

## Validation

`uv run ruff check src tests` and `uv run pytest -q` both clean; demo output unchanged. See
the PR for exact counts.
