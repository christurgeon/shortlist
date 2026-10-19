# 2026-10-19 — Form 4s filed by the company about another issuer

**Kind:** evidence. **Fix:** `aggregate_form4` keeps only filings whose issuer CIK equals the
company's (`issuer_cik`, required keyword); `EdgarSource._fetch_insider` passes `Company(t).cik`.

## Symptom and cause

`Company(t).get_filings(form="4")` lists every Form 4 on the company's CIK. That includes the
Form 4s the company filed as a *reporting owner* of another issuer: Berkshire buying Lennar,
Exxon (10% owner) selling ProPetro, BAC and GS on funds and portfolio companies, GV/Alphabet on
a portfolio company. The aggregator summed them as insider flow in the company itself.

## Before / after

One `latest(40)` fetch per ticker, parsed once. The same list went through `origin/main`'s
`_form4.py` and the fixed one. Run 2026-10-19, cutoff 2026-04-19, conviction off (the
`config.yaml` default). "Other" counts in-window filings whose issuer CIK is not the company's.

| Ticker | In-window own / other | net_value_6m before | after | buys b/a | sells b/a | trades b/a |
|---|---|---|---|---|---|---|
| BRK-B | 10 / 6 | +356.4M | −19.5M | 50 / 5 | 3 / 1 | 53 / 6 |
| XOM | 3 / 1 | −276.6M | none | 0 / 0 | 1 / 0 | 1 / 0 |
| BAC | 17 / 23 | −6.72M | −6.72M | 30 / 0 | 46 / 1 | 76 / 1 |
| GS | 25 / 6 | −20.6M | −36.7M | 12 / 0 | 83 / 30 | 95 / 30 |
| GOOGL | 39 / 1 | −6.30M | −0.23M | 0 / 0 | 6 / 4 | 6 / 4 |
| JNJ (control) | 25 / 0 | −70.2M | −70.2M | 0 / 0 | 11 / 11 | 11 / 11 |
| GE (control) | 24 / 0 | −7.18M | −7.18M | 0 / 0 | 8 / 8 | 8 / 8 |

`shortlist --tickers XOM,BAC,GS,GOOGL,JNJ`, insider sub-score before → after: XOM 6.2 → 0.0,
GS 21.0 → 17.8, GOOGL 67.5 → 67.6, BAC and JNJ unchanged. XOM now has no EDGAR trades, so
its insider block comes from Finnhub alone: `net_value_6m` is missing and MSPR (−0.33) sets
the score.

## Window truncation (not changed here)

`_FORM4_FETCH_LIMIT = 40` is applied before the filter, so other-issuer filings use up slots.
For BAC the 40th filing is from 2026-05-06, and for GOOGL from 2026-07-28. Both are after the
2026-04-19 cutoff. For these names `net_value_6m` and the counts cover less than the stated
6 months and are understated. The result is a wrong value, not a missing sub-score.
BRK-B, XOM, GS, JNJ and GE reached the cutoff.

## Open follow-up: the filing-events section

`build_events_section` has the same contamination. Schedule 13D/13G and Form 144 filed *by* the
company about another issuer set the company's `activist_13d`, `passive_13g` or
`planned_insider_sale_144`. Headers checked live:

- BRK-B: SCHEDULE 13G/A 2026-10-07 on Lennar sets `passive_13g`.
- BAC: SCHEDULE 13D/A 2026-10-15 on a Nuveen fund sets `activist_13d`, and SCHEDULE 13G
  2026-10-07 on Accendra Health sets `passive_13g`.
- GS: SCHEDULE 13G/A 2026-10-07 on Nebius sets `passive_13g`.
- XOM: SCHEDULE 13D/A and Form 144 on ProPetro (2026-05-20) are now outside the 90-day window.

The fix needs one header request per filing to read the subject company, so it is not in
this change.

## Not rewritten

Snapshots accumulated before this fix keep the contaminated insider fields for these names
on every past date. The snapshot-replay backtest axis reads them as they are. Treat insider
fields for BRK-B, XOM, BAC, GS and GOOGL in earlier snapshots as unreliable.

## Follow-up: both open items closed

Both items above were fixed the same day in `data/sources/edgar.py`, with no new SEC request.

**Filing-event flags.** On a 13D/13G (including /A) or Form 144 row of the submissions index,
the `fileNumber` column comes from the subject company's header block (`005-…` for 13D/G, the
issuer's `001-…` or `000-…` for 144). The filer block has none, so the row is blank when the
company is only the filer. `is_filed_about_another_issuer` drops such rows before the
`index_limit` slice, so they no longer use up slots. The earlier line above, "The fix needs one
header request per filing", was wrong: the rule needs no request at all. Checked against the
filing headers: 498 rows for the plan (16 tickers, 2021–2026) and 238 more in review (asset
managers, ADRs, holdcos, small caps, back to 2016), 0 mismatches. A row where the company is
both filer and subject carries the subject's number and is kept (STT `0000093751-23-000424`).
The rule is limited to the three ownership reports because CORRESP rows are always blank;
widening it would drop the company's own SEC correspondence. A blank or missing number is
treated as not attributable and dropped, which can at worst lose an advisory line.
`tests/test_edgar_other_issuer_live.py` pins eight accessions against their headers.

| Ticker | Flags before | Flags after | `recent` before → after |
|---|---|---|---|
| BRK-B | passive_13g, 8k | 8k | 4 → 1 |
| BAC | activist_13d, passive_13g, 8k, shelf | 8k, shelf | 35 → 3 |
| GS | passive_13g, 144, 8k | 144, 8k | 79 → 7 |

GOOGL, XOM, JNJ and GE are unchanged; XOM's `passive_13g` is a 13G filed on XOM itself.
`last_report_filed` is unchanged for all.

**Form 4 window.** `_fetch_insider` now asks edgartools for Form 4s filed on or after the
cutoff, sorted newest first (an in-memory filter on the already-loaded submissions table), and
parses at most `_FORM4_FETCH_LIMIT = 200`. Past the cap, `raw["form4_truncated"]` is set; it is
not an error. In-window counts: BRK-B 16, BAC 47, GS 31, XOM 4, GOOGL 90, JNJ 25, GE 24; the
heaviest of 35 names checked was DELL at 190. Result: GOOGL −0.23M / 4 sells → −4.45M / 39
sells (insider sub-score 67.6 → 67.5). BAC is unchanged (−6.72M, 0 buys, 1 sell): its 7
missed filings held no own open-market trades. BRK-B, GS, XOM, JNJ and GE are unchanged.

Side effect: `latest(40)` returned `None` for a company with no Form 4 on file and a bare
filing for exactly one, and `aggregate_form4` then raised. ASML (and other foreign private
issuers) had EDGAR coverage `error` with `'NoneType' object is not iterable`; it is now a clean
empty insider result with EDGAR coverage `ok`.

Cost: a first parse makes 2 requests (the filing `.txt`, disk-cached, and the reporting
owner's submissions JSON, not cached between parses); a re-parse makes 1. At the cap that is
about 400 requests on the first run and about 200 per later run for one ticker. On the 42-name
accumulate watchlist, 15 names have more than 40 in-window Form 4s (VZ highest at 112), which
adds 386 parses per run: about 770 requests on the first run and about 390 on each later run.

**Rejected.** Reading `f.header` per filing downloads the full `.txt` (100–500 KB per 13G);
`-index-headers.html` is a few KB, but the file-number rule needs no request. The file-number
rule cannot replace the issuer-CIK filter for Form 4. On Form 4 rows the pattern is reversed
and not clean: of BAC's 47 in-window Form 4s, 20 own-issuer rows are blank, 25 other-issuer
rows are numbered, and 2 own-issuer rows are numbered (BRK-B: 10 own blank, 6 other numbered).
`aggregate_form4` keeps reading the issuer from the parsed form.

Snapshots accumulated before this follow-up keep the false 13D/13G/144 flags and the
shortened insider window; they are not rewritten.
