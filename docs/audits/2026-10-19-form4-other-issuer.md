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
