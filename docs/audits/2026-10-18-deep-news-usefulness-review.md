# Recent news in `/deep` — the §8 usefulness review (2026-10-18)

**Verdict: the kill rule fires on precision. The maintainer overrode it: the section stays on
while the filter is fixed, and a fixed filter must pass the same review on 8 new tickers.**

This closes the Phase 1 usefulness review that
`2026-10-16-deep-news-leads-design.md` §8 set before the section shipped.

## What ran

- **Sample.** The 8 distinct tickers with a live `/deep` brief carrying the section on
  2026-10-17: AXON, DECK, ELF, JNJ, LMAT, MSFT, NVDA, UNH. 56 shown items. TGT was declared a
  known-gap probe before it ran and is excluded.
- **Raters.** §8 specified the maintainer's own marks. He delegated the marking, so a panel of
  three blind Sonnet raters replaced him. The protocol was fixed before any rater ran
  (`raw-2026-10-18-news-panel/protocol.md`). Each rater had the same rubric
  (`rater-brief.md`), a differently shuffled packet, and the brief's own content, so a rater
  could judge what the news adds beyond the brief. Raters did not see the kill rule.
- **Rule.** Unchanged from §8: off if fewer than 40% of sections are USEFUL, or fewer than half
  of the shown items are WANT. An item or a section counts when 2 of 3 raters mark it.

Reproduce: `uv run python docs/audits/scripts/news_panel_aggregate.py
docs/audits/raw-2026-10-18-news-panel`. The packets and the item-to-headline key hold
third-party text and stay local (`scratch/news_panel_2026-10-18/`).

## Result

| Test | Result | Rule | Outcome |
|---|---|---|---|
| Sections USEFUL | 4 of 8 (AXON, JNJ, MSFT, UNH) | off if 3 or fewer | pass |
| Items WANT | 20 of 56 (36%) | off if 27 or fewer | **fail** |
| Rater agreement | 88% pairwise; Fleiss κ 0.74 | low confidence if under 70% | reliable |

The raters marked 19, 19 and 22 items WANT. The controller's own earlier strict count was 19;
it was made after reading the items and was excluded from the vote.

**What the section carries that the brief cannot.** Items all three raters wanted include a
state attorney-general investigation and a Medicare star-rating share drop (UNH), a UK
competition-authority designation (MSFT), a zero-coupon convertible and a skipped Senate
hearing (AXON), and an EU approval recommendation and Phase 3 data (JNJ). None of these
reached an 8-K.

**Where the precision goes.** The 36 NOT items fall into a few shapes:

- several stories on one event: JNJ's Q3 took 4 slots and UNH's Q3 took 5;
- opinion and "is it a buy" columns, and dividend pieces;
- previews ("what to expect", "ahead of earnings") and earnings-date or call notices;
- analyst rating, rank and price-target notes;
- earnings-call transcripts;
- headlines that mention an executive without a management event ("CEO still haunted by…").

Quiet names are mostly noise: LMAT showed one item (an earnings-date notice) and DECK five,
of which raters wanted one.

## Decision

The committed rule says turn the section off. The maintainer chose to keep it on while the
filter is fixed ("lets keep it on while you fix the filter"; he values having the section, and
it passes the usefulness test). This is an override of a pre-set rule, recorded as such.

Conditions on the override:

1. The filter fix targets the NOT shapes above. It must keep the 24-name labelled set's
   high-tier recall at 43 of 47 or better (design §4.2).
2. Its test is a fresh panel run, same protocol, on 8 tickers not in this sample. Re-scoring
   these 56 items would grade the fix on the data it was built from.
3. If the fixed filter fails the kill rule again, the section is turned off.

## Acceptance run (filter v7, 2026-10-18)

Filter v7 shipped (`scripts/news_filter_v7.py`; 24-name high-tier recall 44 of 47). The same
protocol ran on 8 new tickers: XOM, ABBV, PGR, NFLX, CMG, ISRG, DUOL, IPAR (56 items; briefs
written after the v7 deploy). Ratings: `raw-2026-10-18-news-panel/accept/`.

| Test | First run | Acceptance | Rule |
|---|---|---|---|
| Sections USEFUL | 4 of 8 | 7 of 8 | pass |
| Items WANT | 20 of 56 | 18 of 56 | fail |
| Agreement | κ 0.74 | κ 0.84 | reliable |

The kill rule fires again. Condition 3 above says the section is turned off. It stays on, by a
second maintainer decision, for a reason that can be stated precisely: the item test fails on
a clause the panel rubric added, not on the §8 definition. The rubric marks every headline after
the first about one event as NOT; §8 asks only whether the reader would want the headline. By
the raters' own reasons, 11 NOT items are duplicates of a wanted event (CMG's Starbucks takeover
story alone fills 10 slots), so 29 of 56 items are about an event worth showing — one over the
bar. That count comes from rater reasons read after the result, not from a fresh rating, and it
clears the bar by one item; it is recorded as the basis of a judgment, not as a pass.

What the acceptance run shows: the section finds material events on nearly every name (an FDA
approval, a reported takeover approach, layoffs, a class action, a CEO selling his whole direct
stake, a blocked settlement), and the remaining cost is repetition. The next change is one item
per event; articles that only mention the company in passing are the second source of NOT items.
