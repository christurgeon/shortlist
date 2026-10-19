# EDGAR clients (`shortlist/edgar/`)

A small library of SEC EDGAR clients: 13F holdings, Form 4 insider trades, Schedule 13D
filings and 8-K items. The package docstring (`edgar/__init__.py`) is the summary; this file
lists the verified facts about SEC data that a future change must not "fix" back.

**No production path calls these clients.** `/screen` and `/deep` do not import them. They
exist so the data is reachable by hand during research, and `backtest/edgar_history.py` uses
`stake_pct.py`. The harness `EdgarSource` is separate (see `HARNESS.md` → EDGAR source).

| Module | What |
|---|---|
| `thirteenf.py` | 13F information-table parse, position aggregation, new-position and material-add diffs |
| `insider.py` + `dera.py` | Form 4 parse; DERA owner index for officer and director roles |
| `index.py` | EDGAR daily-index client (Form 4, initial 13D and `/A` amendment streams) |
| `eightk.py` | 8-K item matching over EFTS rows |
| `quality.py` | Filing classifiers: SPAC/shell, initial 13D, affiliate, known activist |
| `calendar.py` | Filing-date calendars |
| `cusip_map.py`, `cik_tickers.py`, `symbology.py` | CUSIP→ticker and CIK→ticker resolution |
| `stake_pct.py` | 13D/A cover-page percent-of-class extraction |
| `sec_throttle.py` | The one process-wide sec.gov rate budget |
| `models.py`, `_ticker_rules.py` | Shared dataclasses; ticker-suffix and 8-K item normalization |

**CI checks parse shapes, not SEC.** The live fetch tests are marked `live` and skip by
default, so CI does not notice when SEC or edgartools changes a response shape. Run
`SEC_IDENTITY=you@example.com uv run pytest -m live` before you trust a client after a long gap.

## The shared sec.gov throttle (`edgar/sec_throttle.py`)

One process-wide minimum-interval budget (`DEFAULT_MIN_INTERVAL_S`), set below SEC's ~10
requests/second fair-access limit. Every sec.gov client here passes a consumer label
(`throttle("edgar_form4")`); unlabelled calls count as `unattributed`.

- **Never give a client its own throttle.** A per-client throttle cannot bound the process's
  total rate. One unthrottled sweep once drew 429s from SEC and failed every other client
  in the run (`docs/audits/2026-08-05-discovery-funnel-audit.md` §4).
- **Concurrency buys nothing.** One serial worker already exceeds the rate limit, so the
  limit, not I/O wait, is the whole constraint.
- **Not routed through it:** the harness `EdgarSource` (its own semaphore) and `data/efts.py`
  (its own throttle; a different host, `efts.sec.gov`).

`cik_tickers.load_raw_company_tickers` retries transient failures, then falls back to a
cached ticker map up to 7 days old. The built map is memoised per day
(`reset_resolver_cache()` clears it).

## Daily index and Schedule 13D (`edgar/index.py`)

- The modern form label is **`SCHEDULE 13D`**; the legacy `SC 13D` is still accepted.
- `get_filings` returns **every row twice**. Deduplicate by accession before any header fetch.
- The **subject company is the target**. The filer is the activist, so the filer's ticker is
  the wrong one.
- In a Form 4 index row the CIK is often the *reporting owner*, a person. The issuer's ticker
  is only in the document's `<issuerTradingSymbol>`, so do not filter rows on whether the CIK
  resolves to a ticker.
- `max_filings` truncates a **structured, not random** prefix of the day's filings. Shuffle
  or sort before any cutoff, or the sample is biased.
- The daily index is not published until about 02:00 UTC, so fetchers walk back to the last
  published day.
- `company_tickers.json` lists the **common stock first** for each CIK. Resolution takes the
  first entry; a plain suffix rule would bind some issuers to a preferred or foreign sibling.

## 8-K items via EFTS (`edgar/eightk.py`, `data/efts.py`)

The daily index has no item codes; EFTS full-text search returns them inline.

- EFTS needs **browser-like `User-Agent` and `Accept` headers**.
- Intermittent 500s are normal: retry 5xx only, never 4xx, with capped backoff.
- **EFTS lags.** Today's date returns `total: 0`, so callers walk back and treat a day as
  final only after the lag.
- `forms=8-K` **also returns `8-K/A` rows.** Drop `file_type != "8-K"` wherever an amendment
  would double-count.
- Pagination stops at **`from + size ≤ 10,000`**. Split any range near that total by date.
- Resolve CIK→ticker only through `cik_tickers` (live) or `symbology` (point-in-time), never
  from `display_names`.

`NEGATIVE_ITEMS` (`1.03, 2.04, 2.05, 2.06, 3.01, 4.02, 5.01`) and `DEFAULT_ITEM_SETS` in
`eightk.py` are the item sets the clients match.

## 13F holdings (`edgar/thirteenf.py`)

Diffs a fund's latest exact `13F-HR` against the one before it (`/A` amendments excluded).

- One holding can span **several `<infoTable>` rows** (voting-authority splits). Aggregate by
  CUSIP and sum `value`.
- Drop rows with a `putCall` (options) and rows where `sshPrnamtType != "SH"` (convertible debt).
- The information table is the filing's `.xml` that is **neither `primary_doc.xml` nor
  `xslForm13F…`**; an `index.json` fetch is needed to find it.
- **Detect adds on share count (`sshPrnamt`), never `value`.** `value` is quarter-end market
  value, so a price rise alone would look like buying.
- Use only active filers' CIKs. Old adviser shells still exist on EDGAR and return stale data.
- A caller must mark a filing processed even when its diff is empty, or it re-downloads both
  tables every day.

**CUSIP→ticker** (`edgar/cusip_map.py`) abstains rather than guesses: (1) SEC
fails-to-deliver files, newest settlement wins; (2) exact normalized issuer-name match against
`company_tickers.json`; (3) `None`. It yields a ticker but no CIK.
