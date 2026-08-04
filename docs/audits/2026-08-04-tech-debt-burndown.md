# TECH-DEBT.md burn-down — 1 item fixed, 3 inherited to TODO.md, file retired

**Date:** 2026-08-04 · **Change:** `TECH-DEBT.md` deleted; one latent correctness bug fixed
(`_fmp_insider` three-way classification + abstain guard); three open items migrated to
`TODO.md`; three "investigated — do not fix back" verdicts converted into code comments at
the sites they protect.

Committed evidence of record, written because deleting the file would otherwise destroy the
most valuable thing in it: the *negative* results — the documented reasons certain obvious
fixes are wrong. Per CLAUDE.md ("prefer making the guard mechanical over documenting it
harder"), those verdicts now live next to the code rather than in a file nobody opens.

> **This document was materially corrected after an adversarial review** (2026-08-04, two
> independent Opus agents). **Five** claims in the first draft were wrong or overstated, and
> the corrections are recorded inline below rather than silently rewritten — the errors are
> themselves the lesson. See "What the review caught" at the end.

## What the file actually contained

**13 entries. 10 were already closed** — 7 `RESOLVED` plus 3 investigated/by-design no-op
verdicts. ~77% closed, and only about **half the file by line volume** (the three open items
occupy ~82 of 173 lines). It was largely a historical record, not a backlog.

> **CORRECTION.** The first draft said "14 entries, 11 closed (7 RESOLVED, 4
> INVESTIGATED-no-op)", i.e. ~90%. That **double-booked the FMP-insider item**: its long
> review blockquote was counted as a closed entry while its parent `###` heading was counted
> as open — even though the blockquote's own first sentence reads "stays deferred". The
> honest split is 7 + 3 = 10 closed of 13. The "~90%" figure was wrong on its own arithmetic
> too (11/14 = 78.6%).

Before acting, the three durable no-op verdicts were re-verified against `main`. **Two hold;
one was refuted.**

| Verdict (as recorded) | Re-verified 2026-08-04 |
|---|---|
| `backtest/metrics.py` `else 0.0` bucket branch is unreachable | **HOLDS.** Brute-forced over `len(clean) ∈ [4,3000) × n_buckets ∈ [1,40)` — zero empty buckets. `len(clean) < 4` early-returns; the collapse loop exits with `nb == 2` or `len//nb >= 2`, so `size >= 2` and every bucket spans ≥1 row |
| `backtest/cli.py:_load_histories` serial fetch is by design | **HOLDS mechanically; rationale was OVERSTATED** — see correction below |
| `ret_1m`/`ret_3m`/`ret_6m`/`ret_12m` are dead — "read by nothing" | **REFUTED** — see correction below |

> **CORRECTION — the `ret_*` fields are NOT write-only.** They are absent from
> `_NON_SIGNAL_FIELDS`, so `_signal_fields(Price)` includes them and both
> `TickerSnapshot.coverage()` and `.missing()` read all four — **4 of the 13** Price
> denominator entries. That propagates: `accumulate.py` gates storage on `cov <
> min_coverage` and classifies THIN vs CAPTURED off the same ratio. Measured against the
> 1,432-snapshot store, deleting them shifts mean coverage **+0.016** and **flips
> THIN/CAPTURED for 233 snapshots (16%)**; `ret_6m` is populated in ~700 of them, so it is a
> *filled* numerator entry, not padding. The first draft's stated reason for keeping them —
> "serialized snapshot state read back by the accumulation store" — is **also wrong**:
> `from_dict` explicitly drops unknown keys, so deletion would be safe on the read path. The
> real reason is the coverage denominator. The original 2026-06-15 note was narrower and
> correct ("not read by the bridge, scorer, backtest, or reports"); the error was
> generalising it to "read by nothing".

> **CORRECTION — the serial-fetch rationale.** The first draft cited CLAUDE.md's Yahoo-WAF
> gotcha as evidence that "a burst of concurrent requests risks the fingerprint block". That
> section says the block is **header-shape based, "not throttling"**, never mentions
> concurrency, and describes the **screener** endpoint — while `_load_histories` calls the
> **chart** endpoint. The decision to stay serial is still right (offline batch, unofficial
> endpoint, `prices.py` carries its own "baits the WAF" note); only the sourcing was
> invented. The comment now says so.

## Item — FMP insider non-`P` codes (FIXED)

`providers/_fmp_insider.py:is_buy()` returned True only for `P`-prefixed codes and
`net_value()` did `net += v if is_buy else -v`, so awards (`A`), option exercises (`M`),
gifts (`G`), tax-withholding (`F`) and conversions (`C`) all counted as **sells** — both
subtracting from `net_value_6m` and inflating `sell_count`. The sibling EDGAR path
(`_form4.classify_code`) three-ways correctly and skips `other`, so the two insider paths
disagreed, biasing FMP's net flow bearish (a routine option exercise read as selling).

**The trap that kept this deferred.** The tempting one-liner — reuse `_form4.classify_code`
on both paths — is wrong on two counts, both re-confirmed here:

1. `classify_code` does an **exact** `c == "P"` because it parses edgartools' bare
   single-letter `Code` column. FMP's `transactionType` is an **enriched string**
   (`"P-Purchase"`). Feeding `"P-PURCHASE"` to it returns `"other"` — a naive swap
   classifies **100% of FMP transactions as non-trades**.
2. That all-`other` result **clobbers EDGAR**. `harness_sources` orders `fmp` before
   `edgar`, and `_merge_insider` takes the coupled txn facts wholesale from the first source
   with a present field. `_is_present(0)` is `True`, so `Insider(net_value_6m=0, …)` **wins
   the merge and discards EDGAR's authoritative data** — the opposite of the intent.

**The fix, after review, has four parts** (the first draft had two):

- `_fmp_insider.classify_tx()` — its own three-way, splitting FMP's `<CODE>-<Description>`
  form on the first dash and matching the leading letter.
- A **`found`-style abstain guard** in `data/sources/fmp.py` so a batch with no real P/S
  trade leaves `snap.insider` unset rather than zero-clobbering.
- **Non-trades are dropped BEFORE the 60-row window**, not inside it. Review demonstrated
  *window starvation*: 59 award rows + 5 purchases yielded `buy_count == 1`, silently losing
  four real purchases. Pre-existing, but the fix half-addressed it and would have left the
  caveat documented only in dead code.
- **An unpriced row cannot vouch for the section.** `tx_value` returns 0 when `price` is
  null, so a single real `S-Sale` with no price set `found = True` and emitted
  `Insider(net_value_6m=0, sell_count=1, …)` — which still wins the merge on a fabricated
  zero. Review demonstrated EDGAR's real −$4M aggregate being discarded this way. Such rows
  now count toward buy/sell counts (a count needs no price) but cannot make the section
  present.

Both new behaviours are pinned by tests confirmed non-vacuous — they fail against the
pre-fix code and pass after.

**Unverified assumption, stated up front.** `classify_tx`'s `<CODE>-<Description>` split
rests on **no recorded evidence in this repo**. `fmp.fetch_insider` ships `false`, so the
endpoint is never called and `.cache/http.sqlite` holds no insider payload; the test fixtures
assert the same assumption rather than evidencing it. The first draft called those fixtures
"the real-format contract", which overstated what they prove. A dashless payload
(`"Purchase"`) would classify as `other`, and an all-`other` batch abstains — so the failure
mode is **fail-safe** (EDGAR wins) rather than wrong numbers. Re-check against a real
response before relying on FMP insider data on a paid tier.

**Scope of effect.** This is **output-neutral today**, but for a stronger reason than the
first draft gave: `config.yaml` ships **`fmp.fetch_insider: false`** and `sources/fmp.py`
deletes the insider section before fetching, so the changed block never executes — a
config-level guarantee independent of key or tier. (The free tier's 402 is a redundant
second backstop.)

> **CORRECTION.** The first draft said "there is no `FMP_API_KEY` on this host, so the path
> never fires". **There is one** — the check that concluded otherwise used a grep anchored to
> `^[A-Z_]*=`, which silently skipped every `export `-prefixed line in `.env`. Both
> `FMP_API_KEY` and `FINNHUB_API_KEY` are present. Key absence was never the reason.

The **scored** blast radius is `net_value_6m` alone: `buy_count`/`sell_count` never reach the
scorer (`bridge.py` plumbs only `net_value_6m`, `sentiment_mspr`, the conviction aggregates
and `insider_recent`), and `heavy_insider_selling` runs off Finnhub MSPR. One **unscored**
side effect the first draft missed: `insider_recent` now omits non-trade rows, which changes
the `research.insider_detail` context line on a paid tier — prompt-only and unscored, but a
real output change.

**Dead code removed.** With `fmp.py` switched to `classify_tx`, both `is_buy` and
`net_value` had no production caller. `net_value` was worse than merely dead — it
re-implemented the netting loop *without* the 183-day cutoff and *without* the abstain
guard, a second subtly-divergent definition of "FMP net insider flow" sitting next to the
live one. Both were deleted rather than left as a trap; the module docstring now records why
the netting deliberately lives in `sources/fmp.py`.

## Items inherited to `TODO.md`

Three, not two — the first draft dropped one.

- **Momentum Stage 0 prize-bound re-run.** Needs ~1,000 FMP calls against a 250/day free
  cap: a paid Starter tier or ~5 days of split quota. A **measurement**, not a code change.
- **Finnhub `roiTTM` → `roic`.** `--source xbrl` cannot measure it, but the **snapshot-replay**
  path can in principle — the store already holds ~706 Finnhub-provenance `roic` snapshots
  over 43 capture days. Blocked on code work, not on impossibility.
- **Should `fmp` outrank `edgar` for the insider transaction group?** A live deferred
  decision from the FMP-insider review note, which the first draft acknowledged but gave no
  home — it would have been deleted with the file.

> **CORRECTION — both "blocked" framings were wrong.** The first draft said the momentum item
> was blocked because "`oracle-prod` is Yahoo-IP-blocked" and there is no FMP key. Live-probed
> 2026-08-04: `v8/finance/chart/SPY` returned **200 on 3/3 attempts** with the project's own
> `_UA` (a generic Chrome UA got 429 on the same host — the header-fingerprint effect, not an
> IP ban), and the scout's `yahoo_blocked_until` was an **expired self-clearing cooldown**.
> The FMP key exists and returns `429 Limit Reach` — quota spent, not missing. Only the quota
> cost survives. Separately, the first draft called the `roiTTM` gate **"unsatisfiable"**;
> that is true only for `--source xbrl`, not in general.

## Knowledge preserved mechanically

Comments added where a future reader would otherwise re-litigate a settled question:
`backtest/metrics.py` (the unreachable branch, with the proof and the brute-force result),
`backtest/cli.py:_load_histories` (serial by design — with the corrected rationale, and an
explicit note that the obvious CLAUDE.md citation is a misread), `data/models.py` (the
`ret_*` fields feed no scoring leg **but are coverage-denominator entries**, so deleting them
is not cosmetic), and `data/sources/finnhub.py` (how the `roiTTM` proxy could actually be
measured).

The `ret_*` fields were **left in place** — correctly, but for the reason established on
review rather than the one first written down.

## What the review caught

Five wrong or overstated claims, all in the first draft, none caught by the test suite:

1. The entry count (14/11 → 13/10) — a double-booked item.
2. `ret_*` "read by nothing" — refuted; they are coverage-denominator entries, and the
   stated back-compat reason for keeping them did not exist.
3. "No `FMP_API_KEY` on this host" — a broken grep pattern.
4. "`oracle-prod` is Yahoo-IP-blocked" — a stale assumption; live probe returns 200.
5. "The `roiTTM` gate is unsatisfiable" — true only for one backtest path.

Plus two real code defects in the fix itself (window starvation, unpriced-row zero-clobber)
and one inverted premise in a comment pinned as durable. **A green suite proved nothing about
any of them** — every one was a claim about the world, not about behaviour.

## Validation

`uv run ruff check src tests` clean; `uv run pytest -q` → **2291 passed, 3 skipped, 0
failures**.

> **CORRECTION (2026-08-04, follow-up review).** This section originally read "Net test
> count is +2 vs `main` (2289): four tests removed, six added." Every number was wrong, and
> it mixed units — `2289` is `main`'s *collected* count while `2291` is HEAD's *passed*
> count. Against `94426d6`: **3** tests removed (the 4th, `test_net_value_ignores_other_
> codes_entirely`, was added *and* removed inside the branch and was never on `main`), **8**
> added, **net +5**. `main` collects 2289 = 2286 passed → 2291 passed.
>
> It also cited `shortlist --demo --json` being byte-identical as evidence. It is true but
> proves nothing here: `--demo` runs off `mockdata.py` and never enters `_normalize_fmp`, so
> the changed block cannot affect it. That is precisely the "a green check proved nothing
> about the claim" pattern this document names above — committed one paragraph after naming
> it.
