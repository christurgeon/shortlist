# 2026-10-20 — Form 4 trades counted more than once: amendments and duplicate filings

**Kind:** evidence. **Fix:** `aggregate_form4` (`providers/_form4.py`) skips every Form 4/A
before it parses it. It also counts an open-market (P/S) row once when an identical row is in an
earlier-listed counted filing and either the row is an indirect holding or the two filings share a
reporting-owner CIK. It never merges rows inside one filing.

## Symptom and causes

`EdgarSource._fetch_insider` summed every P/S row of every in-window Form 4 on the company's
list. Two causes made one trade count two or three times:

1. **Form 4/A amendments.** `Company.get_filings(form="4")` also returns `4/A`, because
   edgartools defaults `amendments=True` (`filter_by_form` extends the list with `"4/A"`). Most
   amendments restate or correct trades that the original already reports.
2. **Duplicate filings.** Joint reporters file the same trade separately. Jim, Rob and Alice
   Walton each file a Form 4 for one Walton trust sale of WMT. A form is also sometimes filed
   twice (NFLX, Hoag, 2025-06-02: `0001062993-25-010708` and `-010709` have the same XML).

The DERA path (`edgar/dera.py`, `DOCUMENT_TYPE == "4"`) already excluded 4/A. The retired
`edgar/index.py:fetch_form4_submissions` → `edgar/insider.py:parse_form4_xml` path has the same
amendment issue, but it has no production caller, so it is not changed.

## Method

- **Live, watchlist.** The 42 names in `src/shortlist/data/accumulate_watchlist.txt` plus
  BRK-B. Each ticker went through `EdgarSource._fetch_insider` (cutoff 2026-04-20, cap 200,
  conviction off, the `config.yaml` default). The same filing list also went through
  `origin/main`'s `_form4.py`, so code and data drift are separate. A 3-year scan
  (filing date ≥ 2023-10-21) matched each 4/A to its original: same issuer, same
  reporting-owner CIK set, form `4`, and filing date equal to the 4/A's
  `dateOfOriginalSubmission` (read from the raw XML; edgartools does not parse it).
- **Offline, market-wide.** The cached DERA Form 345 data sets (`.cache/dera/`, 2022q3 to
  2026q1, no network). Window 2023-10-21 to 2026-03-31. 4/As were matched to originals by the
  same rule, with `DATE_OF_ORIG_SUB`. Duplicate filings were grouped by issuer, period of
  report and the full P/S row set (date, code, shares, price, direct/indirect, nature of
  ownership, shares owned after the trade), originals only. Reporting owners come from
  `REPORTINGOWNER.tsv`.
- **4/A sub-classes** in the market-wide table compare the 4/A with its matched original by
  (trade date, shares):
  - *no rows*: the 4/A has no non-derivative transaction rows. Derivative rows were not checked.
  - *recodes the same rows*: every original P/S row reappears in the 4/A under a non-P/S code.
  - *lists only other rows* (partial amendment): no original P/S row reappears in the 4/A.
  - *recodes rows into P/S*: every 4/A P/S row is in the original under another code.

## Before / after, current window

Run 2026-10-20. The other 41 tickers are identical before and after.

| Ticker | Cause | net_value_6m before | after | buys b/a | sells b/a |
|---|---|---|---|---|---|
| PG | 4/A `0000080424-26-000160` restates one sale of `-000158` and corrects its trade date | −7,924,060 | −7,485,069 | 0 / 0 | 18 / 17 |
| NKE | 4/A `0000320187-26-000053` corrects the price of a $1.0M CEO buy; the original was filed 2026-04-14, before the cutoff | −574,654 | −1,574,773 | 1 / 0 | 9 / 9 |

The duplicate-row rule changes no ticker in today's window. The approximate flow-leg effect
(`_norm(net/mcap, -0.0005, 0.0005)`, market cap from the Yahoo chart price × EDGAR shares) is
PG 47.85 → 47.97 and NKE 48.2 → 45.2 points. The insider sub-score moves by about half of that.

NKE was double-counted (2 buys, +$2.0M for one buy) while both filings were in the window,
from 2026-04-22 to about 2026-10-14.

## WMT, fixed 2024 window

Filings 2023-12-30 to 2024-06-30 (183 days), the same list through both versions:

| | sells | net_value | distinct trades counted 1× / 2× / 3× |
|---|---|---|---|
| before | 103 | −$8,325M | 26 / 1 / 25 |
| after | 52 | −$2,888M | 52 / 0 / 0 |

The 2024-05-17 sale of 3,540,250 shares at $64.5545 was counted 3 times before and once after.
On the watchlist, the DERA scan finds 20 such WMT groups from 2023-11 to 2024-09, with $7,435M
of extra sells. The WMT flow leg was probably clamped either way; that is not measured.

## Watchlist 4/As over 3 years

72 4/A among 12,760 Form 4s. 7 are about another issuer (already filtered). 55 have no P/S
rows on either side. The other 10:

| Class | Accessions | Effect before |
|---|---|---|
| identical restatement | NVDA `0001045810-23-000242` ($19.20M), CRM `0001127602-24-022191` ($0.35M), AMD `0000002488-25-000030` ($10.91M) | double count |
| same rows, one field corrected | PG, NKE (above), GOOGL `0000950170-25-110753` (trade date) | double count |
| amends a 2022 original | ADBE `0001104659-24-040732`, `-040729` ($0.87M, $1.11M) | 2022 trades in a 2024 window |
| has no tables (power of attorney only) | GOOGL `0000950170-25-112321` | none |
| adds trades in no original | NFLX `0001062993-25-011280` (part 2 of 2; part 1 filed twice) | counted |

## Market-wide 4/A classes (DERA)

6,708 4/As. Skip versus supersede (replace the matched original with the 4/A):

| Class | n | Skip | Supersede if matched |
|---|---|---|---|
| no P/S on either side | 4,704 | n/a | n/a |
| identical restatement | 620 | right | right |
| same row count, a value corrected | 414 | keeps the original's figures | right |
| 4/A has no non-derivative rows | 182 | right | deletes real trades unless guarded |
| 4/A recodes the same rows to A/F/G/J/M | 131 | keeps P/S rows that are not P/S | right |
| 4/A lists only other rows (partial amendment) | 103 (+11 mixed) | right | deletes real trades |
| 4/A recodes rows into P/S | 83 | misses them | right |
| 4/A adds genuinely new rows | 74 | misses them | right |
| original not in the data (older, or owner set differs) | 159 | right if stale (ADBE type) | wrong if stale |
| different rows | 107 | ambiguous | ambiguous |
| several same-day originals (NFLX type) | 57 | ambiguous | ambiguous |
| 4/A is a subset / superset | 32 / 31 | wrong | right |

**Skip is kept.** By case analysis of the 10 watchlist cases (not a script), a *guarded*
supersede (keep the original when the 4/A has no tables; skip an unmatched 4/A) is right on 9 of
10, the same as skip; both fail NFLX. Neither rule
dominates market-wide. Skip fails by not applying a correction. Supersede fails by deleting real
trades when an amendment is partial, and amendments are not always full restatements. Supersede
also needs a raw-XML regex, owner-set matching and a rule for several same-day originals; skip
needs one condition and no parse. Skip matches the DERA path and the filing-date window. Reopen
this only with evidence that the correction cases matter more than the deletions.

## Duplicate filings (DERA)

- Same owner, form filed twice: 46 groups, 76 extra filings. Watchlist: NFLX 2025-06-02
  ($26.76M).
- Different owners, identical rows (joint reporters): 913 groups, 1,191 extra filings.
  Watchlist: WMT, 20 groups.
- Across all originals in the window, 83 P/S rows have no shares-owned-after value. The key
  can never match them.

Cross-filing matches on the full-row key, by row type: indirect with disjoint owner sets, 3,781
rows; direct with overlapping owner sets, 921; direct with disjoint owner sets, about 324.

**Direct rows merge only when the filings share a reporting owner.** Different insiders can
make the identical direct trade. Direct rows with disjoint owners that match the key: 153 P rows
(73 of them a first purchase, shares owned after = shares bought) and 171 S rows. Examples:
TKO's CEO Emanuel (`0000950170-23-063920`) and President Shapiro (`0000950170-23-063915`) each
bought 12,531 shares at $79.80, holding 12,531 after; three Gouverneur Bancorp directors each
bought 3,000 at $10 in a conversion offering; LuxUrban's CEO and CFO each bought 20,000 at $25.
These are cluster buys, the case `distinct_buyers` exists to count. The joint-reporter duplicates
are indirect (WMT is "I / By Trust"; related fund entities report "I / See Notes"), and a re-filed
form shares its owner (NFLX).

**Rejected: a trade-tuple dedupe** (owner, date, code, shares, price). 2,264 original filings
repeat such a tuple *inside* one filing, and those are distinct trades. The current-window
repeats on META and PEP are this case: META `0000950103-26-015598` sells 82 shares at 728.1 for
two LLCs; PEP `0001061272-26-000004` has two buys of 45,075 at 128.28 with different holdings
after. The shipped key therefore includes security, direct/indirect, nature of ownership and the
holding after the trade, compares rows only across filings, and merges a direct row only
between filings with a common reporting owner.

## What the rules get wrong

- A trade reported only in an amendment is lost (NFLX part 2, the 74 + 83 market-wide
  classes). Hoag's 2025-05-29 sale alone (not NFLX net flow): true −$37.7M; before −$64.4M;
  skip alone −$53.5M; skip + dedupe −$26.8M.
- A 4/A that corrects a value keeps the original's figure (NKE: $118 on $1.0M; PG keeps the
  wrong trade date).
- A 4/A that recodes P/S rows to non-open-market codes leaves the P/S rows counted.
- A 4/A whose original predates the window drops the trade. This matches the filing-date window.
- A row with no shares-owned-after value is never deduped, so such a duplicate still counts
  twice. A footnote marker that differs between copies (`2026-04-13 [F1]`) also prevents a
  match. A direct duplicate filed by a different owner set (or by a filing whose owners cannot
  be read) is not merged. These fail toward counting.
- Two different people's **indirect** holdings still merge when the nature of ownership, the
  trade and the holding after it are all identical. That is the remaining over-merge risk; no
  such case was found among the examples checked.
- The kept copy is the first in list order (newest filing date). Joint reporters usually file
  the same day, so the surviving name and role are those of an arbitrary co-reporter. With
  `insider.conviction` on, joint reporters count as one buyer and the role weight uses that
  reporter's role. This is acceptable while conviction is off (`config.yaml` has no
  `insider.conviction` block).

## Not rewritten

Accumulated snapshots keep the double counts: PG from 2026-08-27, NKE 2026-04-22 to about
2026-10-14, and any snapshot whose window covers the NVDA, CRM, AMD, GOOGL, ADBE, NFLX or WMT
filings above.
