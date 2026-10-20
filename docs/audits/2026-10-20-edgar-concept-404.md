# 2026-10-20 — A filer without the diluted-shares tag is not an EDGAR error

**Kind:** evidence. **Fix:** on a `companyconcept` 404, `EdgarSource` makes one reference
request for a filer known to report `DILUTED_SHARES_TAG` (Apple, CIK 320193). If that
resolves, the 404 means "tag not reported" and nothing is recorded. Otherwise the error
names what the reference returned.

## Symptom and cause

When the statement view has no diluted share-count row, the fallback asks SEC's
`companyconcept` API for the tag. A filer that never reports the tag gets a 404, which was
recorded as `edgar-diluted-shares-concept`. Coverage then marked EDGAR as `error`: the
stderr line said `edgar fetch error`, `/screen` showed "supplied no usable data", and
`/deep` was told EDGAR had failed. Statements had loaded normally for every affected name.

## Why the 404 alone cannot decide

Live, with the repo's User-Agent. Every 404 is `application/xml` with the same S3
`NoSuchKey` body; only the echoed key differs.

| Request | Status |
|---|---|
| BRK-B (10-K, tag not reported), NVO (20-F, IFRS) | 404 |
| Non-existent CIK | 404 |
| Misspelled tag, wrong taxonomy segment | 404 |
| Wrong path (`/companyconcepts/`), wrong root | 404 |
| AAPL (reports the tag) | 200, `tag` echoed |
| No declared User-Agent | 403 |

Treating every 404 as absence would hide a changed URL forever, because CI does not run
live tests. A reference request detects the change on the first 404 of each run.

A decisive reference answer is memoised per `EdgarSource` instance: 200 with the tag, or a
404, a wrong tag or a non-JSON body. A transient one (timeout, 429, 5xx) is not, so the next
404 asks again.

## Before / after

`collect([...], ["edgar"])` then `snapshot_to_coverage_inputs`. MT is a direct seam call.

| Ticker | Before | After |
|---|---|---|
| BRK-B, NVO, TSM, SAP, RY | `error` (concept 404) | `ok`, no errors, `diluted_shares` still empty |
| HON, GOOGL | `ok`, `diluted_shares` from the fallback | unchanged |
| MT (seam only) | `rate_limited_429` (see below) | `ok`, no errors |

Simulated breaks, no repo edit:

- URL path changed to `/companyconcepts/`: BRK-B, HON and MT all `error`, message ends
  `reference concept CIK0000320193 -> 404`.
- Reference request times out: BRK-B and MT `error` with `-> ReadTimeout`; HON (200) `ok`.

## Related: 402/429 matched inside a CIK

`coverage_adapt` matched `402`/`429` as bare substrings of the whole error string. EDGAR
errors embed 10-digit CIKs, so MT's concept 404 (`CIK0001243429`) mapped to
`rate_limited_429`. It now matches a standalone token. The real httpx 402/429 messages
still map to `gated_402`/`rate_limited_429`.

## Not covered

A deprecated tag would go unnoticed: `companyconcept` returns the full history, so the
reference keeps resolving, and a filer that moved to a new tag reads as absence.
