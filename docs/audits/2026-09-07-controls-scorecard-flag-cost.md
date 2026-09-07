# A `ScoreCard` controls flag costs 22–56% of a `/screen`. NO-GO.

**Date:** 2026-09-07 · **Kind:** verdict (a no-go)
**Script:** `scripts/probe_controls_flag_cost.py`
**Answers:** `TODO.md` §5, second bullet — *"Measure that cost against a real `/screen`
before paying it."* Closes §5 entirely; the 10-Q half closed the same day
(`2026-09-07-tenq-controls-base-rate.md`).

`research/controls.py` surfaces management's adverse ICFR/DCP conclusion on `/deep`. The
open question was whether it should also be a `ScoreCard` flag, visible on `/screen`. It
was left as a cost question, not a design one. **Measured. The cost is real, it is paid on
every ticker, and the benefit accrues to ~5% of them. Not built.**

---

## 1. Baseline: what a real `/screen` costs today

`uv run shortlist --tickers AAPL,MSFT,GOOGL,AMZN,META,NVDA,AVGO,TSLA,LLY,JPM --json`
— the first 10 names of `universe_largecap.txt`, and 10 is the bot's `/screen` cap.
Sources as shipped (`harness_sources`, 8 of them).

| run | wall | note |
|---|---|---|
| cold (`--no-cache`) | **129.1s** | rc=0, 10 cards; 2 tickers `fmp gated (402)` |
| warm (cache on, as the bot serves it) | **120.2s** | rc=0 |

The HTTP cache buys only 7%, so the screen is not FMP-bound — the tail is elsewhere.

## 2. Marginal cost of a harness-side producer

What a flag producer must do that the harness does **not** already do: select the latest
exact-form 10-K (the post-#199 rule) and pull whole-document text. Section-only routes are
closed — `FilingText.combined()` fires on 2 of 7 known positives and `part_ii_item_9a`
returns 0 chars for 3 of 15 filers (`2026-08-23-icfr-adverse-conclusion-detection.md` §5).

Run 1, all 10 tickers:

| ticker | index select | `filing.text()` | chars | verdict |
|---|---|---|---|---|
| AAPL | 1.16s | 3.32s | 260,861 | clean |
| MSFT | 0.68s | 4.72s | 360,878 | clean |
| GOOGL | 0.45s | 3.56s | 447,548 | clean |
| AMZN | 0.86s | 2.37s | 330,186 | clean |
| META | 0.49s | 2.71s | 573,168 | clean |
| NVDA | 0.25s | 1.86s | 410,537 | clean |
| AVGO | 0.13s | 1.78s | 462,755 | clean |
| TSLA | 0.21s | 1.67s | 453,930 | clean |
| LLY | 0.54s | 2.46s | 443,733 | clean |
| **JPM** | **16.69s** | **21.21s** | 790,376 | clean |
| **total** | **21.5s** | **45.7s** | | |

`detect()` itself is 0.019s/ticker — pure CPU, irrelevant to the decision.

**Added serial cost: 67.3s on a 120.2s baseline — +56%.** If the index selection could be
shared with `EdgarSource` rather than re-fetched, the floor is `text()` alone: **+45.7s,
+38%**.

A repeat run over AAPL/MSFT/JPM separates variance from structure:

| | run 1 | run 2 |
|---|---|---|
| JPM index select | 16.69s | **16.57s** |
| JPM `filing.text()` | 21.21s | 3.17s |

JPM's 21s download was network noise. **Its ~16.6s index selection is structural and
repeatable** — `Company("JPM").get_filings(form="10-K")` over a very large filing history.
One name in ten accounts for a quarter to a half of the whole marginal cost.

## 3. Concurrency does not rescue it

The obvious objection is that the harness is async, so this could overlap. Two reasons that
does not save it:

- **A parallel implementation cannot beat its slowest ticker.** JPM alone measured 17–38s
  of sec.gov work.
- **It would grow *unmetered* load.** `edgar/sec_throttle.py:14-18` records that the
  harness `EdgarSource` fetches on its own asyncio semaphore **outside** the process-wide
  `SecThrottle` budget, and that the two paths avoid overlapping only because `screen.py`
  starts research after `run_harness` returns — *"nothing enforces that."* Adding ten
  concurrent multi-hundred-KB document downloads there is the 2026-08-04 failure class that
  `CLAUDE.md` names explicitly: one unthrottled sweep starves every other SEC consumer.

## 4. What the cost buys

All 10 sampled tickers are **clean**, which is what a ~5.3% base rate predicts for 10 large
caps. The cost is paid on 100% of screened names; the flag fires on roughly 5%.

And it is not new information — the finding already reaches the user through `/deep`, one
step later in the same workflow. The flag buys *earlier visibility on ~5% of names*, for a
22–56% wall-time increase on every `/screen`.

**Verdict: NO-GO.** Not a close call at these numbers.

## 5. What would reopen it

- **A cheap prefilter.** Keyless EDGAR full-text search gates the download to the ~5% of
  names carrying a narrow phrase, cutting the expected cost to roughly one light request
  per ticker. Not free: it adds an `efts.sec.gov` dependency to the screen path with its
  own unmeasured per-IP ceiling, and FTS alone is the naive detector the 2026-08-23 audit
  killed — it would gate the download, never replace `detect`.
- **Sharing the filing index with `EdgarSource`**, which would remove the 21.5s selection
  half and JPM's structural 16.6s with it.
- **A materially higher base rate.** At 5% the arithmetic is hopeless; small caps run
  roughly double (10.0% out-of-sample), which is still not enough on its own.

Do not reopen this on the argument that the detector is cheap. `detect()` was never the
cost — the document fetch is.
