# EDGAR clients — implementation notes

"Verified facts, do not fix back" detail for the SEC-EDGAR clients under `shortlist/edgar/`.
This file is the landmine list; the package docstring (`edgar/__init__.py`) is the summary.

Most of these signals' original design docs live under `docs/superpowers/specs/`, which is
**gitignored** (`.gitignore:37`) — not tracked, not guaranteed to survive a fresh clone. This
file is the committed backstop for the facts that used to live only there or in `CLAUDE.md`.

## What each module is

These are **importable clients with no production caller** — nothing on the `/screen` or
`/deep` path imports them. They exist so the data is reachable by hand during research.

| Module | What |
|---|---|
| `thirteenf.py` | 13F infotable parse, position aggregation, new-position and material-add diffs |
| `insider.py` + `dera.py` | Form 4 parse; DERA owner index for officer/director roles |
| `index.py` | shared EDGAR daily-index client (13D initial + `/A` amendment streams) |
| `eightk.py` | 8-K item matching over normalized EFTS rows |
| `quality.py` | filing classifiers — SPAC/shell, initial-13D, affiliate, marquee activist |
| `calendar.py` | filing-date calendars |
| `cusip_map.py`, `cik_tickers.py`, `symbology.py` | CUSIP→ticker and CIK→ticker resolution |
| `stake_pct.py` | 13D/A cover-page percent-of-class extraction (used by `backtest/edgar_history.py`) |
| `sec_throttle.py` | the one process-wide sec.gov rate budget (below) |
| `models.py` | shared dataclasses |
| `_ticker_rules.py` | shared leaf: 5th-letter junk-suffix rule + 8-K item normalization |

**The deal you take by keeping these uncalled.** CI pins their *parse shapes*; it does **not**
catch SEC or edgartools changing shape upstream, because the live fetch tests are
`pytest.mark.live` + `skipif(not SEC_IDENTITY)` and skip by default. Run
`SEC_IDENTITY=... uv run pytest -m live` before trusting a client after a long gap —
`edgartools` `standard_concept` drift has broken extraction once already
([`audits/2026-07-31`](audits/2026-07-31-edgar-concept-match.md)).

## The shared sec.gov throttle (`edgar/sec_throttle.py`)

Process-wide ~6 req/s min-interval budget (`sec_throttle()`, `DEFAULT_MIN_INTERVAL_S =
0.167` — SEC fair access is ~10 req/s; this IP sits at 60% of it because of a recent 429
history). Routed through it: `edgar_index` (Form 4, 13D), `thirteenf`, `cusip_map`,
`cik_tickers`, `dera`. **Not** routed through it: the harness `EdgarSource` (own async
semaphore, `_EDGAR_MAX_CONCURRENCY`) and `data/efts.py` (own throttle, different host —
`efts.sec.gov`). Each call passes a consumer label (`throttle("edgar_form4")`); unlabelled
calls are still counted as `unattributed` so nothing vanishes from the budget.
`RunManifest.sec_requests` records per-run totals — that's what would settle whether one
consumer is starving the others, if it's ever needed again.

**Never give a signal its own `SecThrottle`.** A per-signal throttle can't bound the
*process's* request rate. On 2026-08-04 the Form 4 sweep ran unthrottled, 429'd SEC for the
rest of the run, and the 13D originator / DERA quarterly index / `company_tickers.json` all
failed behind it — zero candidates that night (`docs/audits/2026-08-05-discovery-funnel-audit.md`
§4). `SecThrottle` used to live in `thirteenf.py`, which still re-exports it for back-compat.

**Don't reach for concurrency here.** `full_text_submission()` latency is ~17 ms, so one
serial worker already sustains ~57 req/s — the rate limit, not I/O wait, is the entire
constraint, so a thread pool buys nothing. Two volume "optimisations" were tried and were
**both wrong**: (1) filtering the daily index on whether its `cik` resolves to a ticker
deletes ~80% of real large-cap emissions — the index row's CIK is the *filer* (often a
person, the reporting owner), and the ticker only exists on the document's
`<issuerTradingSymbol>`; (2) `[:max_filings]` truncates a **structured, not random** prefix
of the day's filings, so any future time-budget cutoff must shuffle or relevance-sort first
or it introduces selection bias. Full detail: `docs/audits/2026-08-05-discovery-funnel-audit.md` §10.

`cik_tickers.load_raw_company_tickers` retries (3 attempts, linear backoff), then falls back
to the newest cached index within 7 days rather than abstaining; the built index is memoised
per `(identity, cache_dir, day)` so all five resolver call sites in a run share one map
(`reset_resolver_cache()` drops it). Before this, one transient 429 returned `{}` and bailed
13D / 13D-A / 8-K / buyback / 13F-symbology for the whole session.

## Activist 13D discovery (`edgar/index.py`)

Scans the SEC daily index for fresh **initial SCHEDULE 13D** filings (`/A` amendments
excluded — that's the escalation signal below).

**Verified facts (live-checked 2026-06-28):**
- The modern form label is **`SCHEDULE 13D`**, not `SC 13D` (legacy, ~1/day — both accepted).
- Initial volume runs ~4–12/day.
- `get_filings` returns **every row twice** — dedup by accession before any header fetch.
- The **subject company is the target**
  (`filing.header.subject_companies[0]…cik/.name`); the *filer* is the activist, so the
  filer's ticker would be wrong.
- `company_tickers.json` lists the **common stock first** per CIK, so CIK→ticker resolution
  is first-occurrence-authoritative, with a sibling-relative-only unit/warrant/preferred
  backstop — a blunt suffix rule mis-binds ~54 liquid issuers to `*F`/preferred siblings
  (e.g. EQNR→STOHF).

Math/ingestion: `edgar/cik_tickers.py` (resolver), `edgar/quality.py` (`is_initial_13d` /
SPAC-shell + affiliate-overlap drops / marquee alias boost), `edgar/index.py`
(`activist_stakes_from_records` aggregator + `fetch_recent_activist_records`, with the same
"index not published till ~02:00 UTC → walk back" fallback as the Form 4 path). Tune

## 8-K item extraction (`edgar/eightk.py`)

Discovers 8-Ks whose items contain a configured AND-set (default **1.01∧3.03**) via
**EFTS** (`data/efts.py`, shared leaf — the daily index carries no item codes, EFTS returns
them inline).

**EFTS gotchas (live-probed 2026-07-07, twice):**
- Needs **browser-ish UA + Accept headers** — bot-shaped requests are rejected (keep
  `Accept-Encoding` httpx-decodable).
- Intermittent 500s are normal → bounded retry-on-5xx **only** (never retry 4xx), backoff
  capped at 8s, ≤3 req/s throttle.
- **EFTS lags**: today's date returns `total: 0`, so the walk-back window and the day-cache
  finality rule (`day <= fetched_on − EFTS_LAG_DAYS`) are load-bearing.
- `forms=8-K` filters `root_forms` and **returns `8-K/A` rows too** — the `file_type !=
  "8-K"` drop is mandatory everywhere an amendment would otherwise double-fire the
  originator, re-trigger the veto, or double-count backfill events.
- ES pagination window is **`from+size ≤ 10k`** — any range whose `total ≥ 9,900` splits
  recursively at the date midpoint (earnings-heavy months approach the cap).

`.cache/efts/<day>.json` always stores the complete unfiltered day. **No `display_names`
ticker fallback anywhere** — CIK→ticker only via `cik_tickers` (live) / `Symbology` (PiT
backfill).

**Negative items**: {1.03, 2.04, 2.05,
2.06, 3.01, 4.02, 5.01} are reliably negative over the funnel's 30–90d horizon; a fresh
match drops the candidate loudly between prefilter and select (`funnel.apply_veto`) before
it burns a deep-screen slot. Every match also logs to the firehose as its own signal
(`edgar:8k_negative`, accession-deduped) with its own pre-registered cohort. Two knobs are
**live-only** (the backfill cohort measures neither): `daily_cap` (default 6/day) and a
populated `deny_list` — keep `deny_list` empty and `daily_cap` generous unless re-measuring.

## 13F holdings (`edgar/thirteenf.py`)

Clones **new positions** in a curated set of marquee funds' latest **13F-HR** (`/A`
amendments excluded — a restatement diff would double-fire). Per fund CIK: pick the latest
exact `13F-HR`, diff its holdings against the immediately-prior one, surface each new CUSIP
whose within-book weight clears `min_position_pct` (0.005). Clone return is measured from
the **filing date** (the disclosure lag priced into the literature — this isn't
front-running).

**Verified facts (live-checked 2026-07-09):**
- A single holding can legitimately span **multiple `<infoTable>` rows** (sole/shared/none
  voting split, combined-manager filings) — aggregate by CUSIP, sum `value`.
- Drop rows with a `putCall` (options) and `sshPrnamtType != "SH"` (PRN convertible debt).
- The information table is the filing directory's `.xml` that is **neither
  `primary_doc.xml` nor `xslForm13F...`** — an `index.json` fetch is required to find it.
- **Material adds are detected on share count (`sshPrnamt`), never `value`** — `value` is
  quarter-end market value, so a price rise alone would read as conviction.
- The 7 seed CIKs are live-verified *active* filers — stale /ADV shells (e.g. Baupost
  1054420, Appaloosa 1006438) are the trap; the config comment names them.

**CUSIP→ticker resolver** (`edgar/cusip_map.py`, layered, abstains rather than guesses): (1)
SEC fails-to-deliver files (`cnsfails{YYYYMM}{a|b}.zip`), 2 most recent, cached forever by
filename, most-recent-settlement wins on symbol churn; (2) exact-normalized-issuer-name
match against `company_tickers.json` titles; (3) `None`.

**Seen-accession semantics**: a caller tracking a fund's latest
13F-HR is marked processed even on a zero-new-positions diff, else an empty-diff fund
re-downloads both infotables daily forever — so the state exposes `processed_accessions`,
not emissions. `max_filings_per_day` (default 3) caps processing (13F is quarterly-bursty).
**Known limit:** the CUSIP resolver yields a ticker but no CIK, so 13F emissions carry
`cik=None` — no CIK-based delisting classification, and a backfill cohort is deferred (a PiT
CUSIP→symbology replay would leak post-event symbols). 
