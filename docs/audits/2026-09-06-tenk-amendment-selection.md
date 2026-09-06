# 2026-09-06 — `/deep` picked 10-K/A amendments over the real 10-K

**Verdict: measured bug, fixed.** `_fetch_10k_parsed` took `.latest(1)` off
`Company(ticker).get_filings(form="10-K")`. edgartools returns `10-K/A` rows inside that
form filter, so any filer whose newest 10-K-family filing is an amendment got the
amendment. Most amendments are Part III patches carrying no Item 1 / 1A / 7.

## How it surfaced

A `/deep TSLA` on 2026-09-06 03:11 UTC produced no brief. The bot log showed three
"TenK falling back to legacy parser" lines for Item 1, Item 7 and Item 1A against accession
`0001104659-26-053166`, with only `part_iii_item_10 … part_iv_item_15` available — Tesla's
10-K/A of 2026-04-30. The real 10-K is `0001628280-26-003952` (2026-01-29).

`fetch_10k("TSLA")` returned `None`; `no_10k_reason("TSLA")` then reported `"no 10-K"` for a
company that files one every January.

## Two distinct failure shapes

Sections extracted from the amendment vs. the original, same ticker:

| ticker | form | filed | business | mda | risk |
|---|---|---|---|---|---|
| AMD | 10-K/A | 2026-02-04 | 0 | 42,454 | 0 |
| AMD | 10-K | 2026-02-04 | 59,710 | 37,539 | 131,442 |
| HOOD | 10-K/A | 2026-02-20 | 79,706 | 46,734 | 310,345 |
| HOOD | 10-K | 2026-02-18 | 79,706 | 46,740 | 310,345 |

TSLA/DASH/LUV/MO/CRCL fail **visibly** — every section empty, `has_content()` false, brief
abstains. AMD fails **silently**: one non-empty section is enough for `has_content()`, so the
brief was built with no business description and no risk factors and said nothing about it.
HOOD's amendment is a genuine full refiling and was harmless either way.

The silent shape is the more dangerous one, and it is the reason the fix is *prefer the exact
form unconditionally* rather than *fall back to the amendment when the 10-K is empty* — AMD
never reaches such a fallback.

## Blast radius

Parsed every `10-K` and `10-K/A` row in the EDGAR quarterly form indexes,
2025-QTR1 → 2026-QTR3, and kept CIKs whose most recent 10-K-family filing is an amendment:

- 7,168 CIKs filed a 10-K in the window
- **647** have an amendment as their newest
- **462** of those map to a listed ticker (605 symbols incl. share classes)

Recognizable names: TSLA, AMD, DASH, HOOD, LUV, MO, CRCL, CBZ, RNST, DFH, HIVE, NABL, BKV,
AVXL, PNNT, NATH.

**This is a floor, not a total** — the window starts 2025-QTR1, so a filer whose amendment
predates 2025 is not counted. The visible/silent split was measured on 6 names (4 visible,
1 silent, 1 clean), not across all 462; each check costs a full document parse.

No stored artifact needed invalidation — neither `research/` nor `/opt/shortlist/research/`
held a brief for any affected ticker.

## The fix, and what it costs

`_latest_exact_10k` filters to `form == "10-K"` and sorts by `filing_date`, mirroring
`_prior_year_sections` and `filing_text_change`, which already dropped `/A` rows. The
amendment is used only when the index contains no exact-form 10-K at all.

**Accepted tradeoff:** a genuine full-restatement 10-K/A is now ignored in favour of the
superseded original. Detecting restatements is a separate problem; the measurement above says
amendments are overwhelmingly Part III patches, and two sibling call sites had already made
this same choice.

## Live verification (post-fix)

All resolve to the real 10-K with all three sections non-empty:

```
TSLA 0001628280-26-003952 2026-01-29  business= 45463 mda= 55405 risk= 83678
AMD  0000002488-26-000018 2026-02-04  business= 59710 mda= 37539 risk=131442
DASH 0001792789-26-000013 2026-02-18  business= 17348 mda= 63720 risk=216906
HOOD 0001783879-26-000023 2026-02-18  business= 79706 mda= 46740 risk=310345
LUV  0000092380-26-000004 2026-02-05  business=103844 mda= 73577 risk= 92837
MO   0000764180-26-000017 2026-02-25  business= 23489 mda=149503 risk= 68819
CRCL 0001876042-26-000062 2026-03-09  business= 60522 mda= 91760 risk=195643
AAPL 0000320193-25-000079 2025-10-31  business= 16054 mda= 18018 risk= 68163   (unchanged)
```

## Left open

The AMD shape — a filing that yields one section and zero others — is dodged here but not
guarded against. `has_content()` still passes on any single non-empty section, so a stripped
document reaching the brief through some other path would still go unannounced. Whether
`fetch_10k` should abstain when `business` or `risk_factors` is empty is **not settled** and
was deliberately left out of this change.
