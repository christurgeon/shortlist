# sec.gov denies the short filing-index URL — every research brief skipped as "no 10-K" (2026-10-10)

**What this note is.** Evidence as of its date for one dependency change: `edgartools` from
5.33.0 to 5.61.1. For how the code behaves today, read the code.

## Symptom

`uv run shortlist --tickers MSFT,JPM --research 2` on 2026-10-10 wrote no brief:

```
Qualitative research
  MSFT   skipped: no 10-K
  JPM    skipped: no 10-K
```

with one warning: `HTMLParser failed for 10-K filing (falling back to ChunkedDocument):
'NoneType' object has no attribute 'download'`. No model call was made. The screen itself
ran: the harness reads `data.sec.gov`, which is not affected.

## Cause

`research/filings.py` reads a 10-K through edgartools. edgartools 5.33.0 reads the filing's
index page at the short URL, `/Archives/edgar/data/<cik>/<accession>-index.html`. sec.gov now
answers that form with HTTP 403. edgartools then sees a filing with no documents, every
narrative section is empty, and `fetch_bundle` returns "no usable 10-K".

Measured on 2026-10-10 with `scripts/probe_sec_filing_index.py` (seven requests):

| URL under `/Archives/edgar/data` | status |
|---|---|
| `/789019/0001193125-26-323660-index.html` (MSFT, short form) | 403 "Access Denied" |
| `/19617/0001628280-26-008131-index.html` (JPM, short form) | 403 |
| `/320193/0000320193-25-000079-index.html` (AAPL, short form) | 403 |
| `/789019/000119312526323660/0001193125-26-323660-index.html` (long form) | 200 |
| `/789019/000119312526323660/` (folder) | 200 |
| `/789019/000119312526323660/index.json` | 200 |
| `/789019/000119312526323660/0001193125-26-323660.txt` (full submission) | 200 |

The denial is on the path form: the same host, identity and minute serve the long form, and a
second User-Agent form got the same 403. `Filing.homepage_url` builds the short form in every
release from 5.33.0 to 5.60.0 and the long form from 5.61.0 (released 2026-10-06), read from
the source of 5.34.0, 5.40.0, 5.47.0, 5.54.0, 5.57.0, 5.59.1, 5.60.0 and 5.61.0.

## Change

`pyproject.toml`: `edgartools>=5.61` (was `>=3.0`). `uv.lock`: edgartools 5.33.0 to 5.61.1 and
its dependency httpxthrottlecache 0.3.5 to 0.6.1. No other package version moved. The lock
file was written with uv 0.6.0, which keeps its format (`revision = 1`): a newer uv rewrites
every entry, and the two version changes are then lost in 2,200 lines of diff.

No code changed. A URL patch in this repo was the alternative; it would have kept a release
whose index reader is known to be broken and whose next break we would also have to patch.

## Verification

- `fetch_10k` under 5.33.0: `None` for MSFT and JPM. Under 5.61.1: MSFT business 41,172
  characters, risk factors 81,066, MD&A 51,468; JPM 39,278, 112,862 and 419,380.
- `uv run pytest`: 2601 passed, 32 deselected, 1 xfailed. `ruff check src tests`: clean.
- The SEC-facing live tests under 5.61.1, with `SEC_IDENTITY` and `RUN_LIVE_EDGAR=1`: 20
  passed (`tests/research/test_filings_integration.py`, `test_durability_live.py`,
  `test_edgar_activist_live.py`, `test_edgar_events.py`, `test_edgar_leverage_live.py`,
  `test_edgar_source_financials.py`, `test_eightk_live.py`, `test_proxy_live.py`,
  `test_tenq_part_ii_live.py`).

## Not known, not done

- **When sec.gov began to deny the short form.** The only date is the upstream fix,
  2026-10-06.
- **Whether production was affected.** It runs the same lock file, so it very probably was;
  it was not checked from the VPS.
- The live tests of the other providers (options, news, short interest, government contracts,
  lobbying, earnings) were not run: they do not use edgartools.
- 28 releases were skipped in one step. What covers them is the suite and the 20 live tests
  above, not a reading of the changelog.
- `data/sources/edgar.py` calls `cashflow_statement()`, which 5.61.1 marks deprecated and
  says will be removed in 6.0. The requirement has no upper bound; `uv.lock` is what holds
  the version. Not changed here.
- The hermetic suite cannot catch this class of break. `docs/EDGAR_CLIENTS.md` says so; the
  live test that would have, `tests/research/test_filings_integration.py`, is skipped by
  default.
