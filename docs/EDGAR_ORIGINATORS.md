# EDGAR-index scout originators — implementation notes

Implementation-level "verified facts, do not fix back" detail for the SEC-EDGAR-index-based
scout discovery originators (`scout/signals.py` + their pure leaves), migrated out of
`CLAUDE.md` 2026-08-07 to keep that file a map, not a diary. Design rationale and
enable/disable decisions stay summarized in `CLAUDE.md`; this file is where the hard-won
implementation gotchas live so they don't get silently reintroduced.

Most of these signals' original design docs live under `docs/superpowers/specs/`, which is
**gitignored** (`.gitignore:37`) — not tracked, not guaranteed to survive a fresh clone. This
file is the committed backstop for the facts that used to live only there or in `CLAUDE.md`.

## The shared sec.gov throttle (`scout/sec_throttle.py`)

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

## Activist 13D discovery (`EdgarActivist13DSignal`)

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

Math/ingestion: `scout/cik_tickers.py` (resolver), `scout/quality.py` (`is_initial_13d` /
SPAC-shell + affiliate-overlap drops / marquee alias boost), `scout/edgar_index.py`
(`activist_stakes_from_records` aggregator + `fetch_recent_activist_records`, with the same
"index not published till ~02:00 UTC → walk back" fallback as the Form 4 path). Tune
`scout.activist_13d` + `scout.signals.edgar_activist_13d`.

## 13D/A stake-increase escalation (`EdgarStakeIncreaseSignal`)

Scans `SCHEDULE 13D/A` (+ legacy `SC 13D/A`) amendments for a **material stake increase**.
`scout/stake.py` (pure leaf, the `_form4.py` pattern): percent-of-class parsing,
abstain-never-guess, **max-of-coverpages** aggregation (a joint filing's several
reporting-person cover pages collapse to the group max, applied consistently on both sides
of the delta).

**Verified facts (live-probed 2026-07-17/18):**
- The structured-XML tag is confirmed `<percentOfClass>` (no namespace), via `f.xml()` —
  the late-2024+ 13D/G modernization.
- Legacy (pre-2024) cover pages need the **raw-HTML tier**, not `.text()` — edgartools' text
  rendering drops the value out of a sibling `<div>` for one common filer-agent template,
  which was **74% of all abstentions** on a 30-filing 2022–23 hand-checked sample. Adding
  the HTML tier took that sample's parse rate from **7/30 (23.3%) to 28/30 (93.3%)**, 32/32
  hand-checked values correct across both rounds.

Three accessor tiers, tried in order (`stake_pct_from_filing`): structured XML → raw HTML →
rendered text. `MIN_INCREASE_PP = 2.0` (absolute percentage points, never relative) is a
**code constant** in `stake.py` — the backfill cohort always uses it;
`scout.activist_13d.stake_increase.min_increase_pp` tunes the live signal only. Baselines +
dedup live in `ScoutState` (`stake_baselines`, `stake_increase_seen`, both capped and
forward-compatible). First-sighting amendments seed the baseline and never emit; a live
walker filters SPAC/affiliate rows *before* the doc-fetch budget is spent, so the measured
backfill population is slightly broader than what live emits. Tune
`scout.activist_13d.stake_increase` + `scout.signals.edgar_13d_stake_increase`.

## 8-K discovery + negative-item veto (`EdgarEightKSignal`)

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

**Negative-item veto** (`scout.eightk.negative_veto`, ships ON): items {1.03, 2.04, 2.05,
2.06, 3.01, 4.02, 5.01} are reliably negative over the funnel's 30–90d horizon; a fresh
match drops the candidate loudly between prefilter and select (`funnel.apply_veto`) before
it burns a deep-screen slot. Every match also logs to the firehose as its own signal
(`edgar:8k_negative`, accession-deduped) with its own pre-registered cohort. Two knobs are
**live-only** (the backfill cohort measures neither): `daily_cap` (default 6/day) and a
populated `deny_list` — keep `deny_list` empty and `daily_cap` generous unless re-measuring.
Tune `scout.eightk`.

## Buyback-authorization discovery (`EdgarBuybackSignal`)

Extends the shared EFTS leaf with a generic `q=` exact-phrase path
(`fetch_phrase_day`/`fetch_phrase_window`, own cache namespace
`.cache/efts_buyback/<phrase-hash>/`) to match a curated verb-anchored phrase set
(`scout/buyback.py:DEFAULT_PHRASES`, e.g. "approved a new share repurchase program" — the
verb anchor excludes "purchases under our existing program" boilerplate). The three existing
no-`q` EFTS consumers (8-K originator, veto sweep, 8-K backfill) stay byte-identical
(`tests/test_data_efts_phrase.py`).

**Phrase precision**, live-probed 2026-07-09: **29/30 hand-classified hits (~97%)** were
genuine new/expanded authorizations over a 10-week window (the one false positive was a
"previously authorized … implement" restatement notice) — well above the 70% implementation
floor. Volume is lumpy, ~4/wk. Filer IS the subject (no header fetch needed). EFTS returns
relevance order when `q` is present, so the signal re-sorts by `file_date` DESC before
`daily_cap` (keeps the freshest authorization, not an older higher-scoring one).

**Outcome:** the pre-registered backfill cohort (`preregister/edgar_buyback_auth.yaml`)
**killed** the signal on evidence — 2026-07-11: scored/gated FF3 alpha −0.84%/mo, 90% CI
entirely negative (`docs/audits/2026-07-11-buyback-backfill-kill.md`). Ships **disabled**.
Tune `scout.buyback` + `scout.signals.edgar_buyback`.

## 13F marquee-fund cloning (`EdgarThirteenFSignal`)

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
- The 7 seed CIKs are live-verified *active* filers — stale /ADV shells (e.g. Baupost
  1054420, Appaloosa 1006438) are the trap; the config comment names them.

**CUSIP→ticker resolver** (`scout/cusip_map.py`, layered, abstains rather than guesses): (1)
SEC fails-to-deliver files (`cnsfails{YYYYMM}{a|b}.zip`), 2 most recent, cached forever by
filename, most-recent-settlement wins on symbol churn; (2) exact-normalized-issuer-name
match against `company_tickers.json` titles; (3) `None`.

**Seen-accession semantics** (`ScoutState.thirteenf_seen_accessions`): a fund's latest
13F-HR is marked processed even on a zero-new-positions diff, else an empty-diff fund
re-downloads both infotables daily forever — so the state exposes `processed_accessions`,
not emissions. `max_filings_per_day` (default 3) caps processing (13F is quarterly-bursty).
**Known limit:** the CUSIP resolver yields a ticker but no CIK, so 13F emissions carry
`cik=None` — no CIK-based delisting classification, and a backfill cohort is deferred (a PiT
CUSIP→symbology replay would leak post-event symbols). Tune `scout.thirteenf` +
`scout.signals.edgar_13f`.
