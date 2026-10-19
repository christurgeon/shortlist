# One item per event in the `/deep` news section (2026-10-18)

**Verdict: ship a deterministic merge pass after the seed clustering (filter v8). The LLM
grouping is not adopted. Articles that only mention the company stay out of scope.**

## The problem

The acceptance panel (`2026-10-18-deep-news-usefulness-review.md` → Acceptance run) found the
section useful on 7 of 8 names. 11 of 56 shown items were rated not wanted only because they
repeated an event already shown: CMG filled all 10 slots with one reported takeover approach.
The v7 clustering joins an article to a cluster only when it shares two capitalized words or
numbers with the cluster's first article, so differently worded coverage stays separate.

## What ran

Each option replaced only the clustering step of the frozen v7 filter, and all were measured
by one harness (`scripts/dedupe_2026-10-18/`):

- **24 labelled names:** the labels group articles into events, which gives a ground truth for
  merges. The harness reports pairwise merge precision and recall, the labelled events hidden
  behind another event, distinct events shown, duplicate items, and the committed guard
  (high-tier recall at K=10, at least 43 of 47).
- **8 acceptance names:** the shown items were hand-tagged by event, so duplicates, real events
  and non-events can be counted.

| Option | Pair precision / recall | Events v7 showed, now hidden | Duplicate items (24) | High-tier recall | Acceptance: events / duplicates / junk |
|---|---|---|---|---|---|
| v7 (status quo) | .65 / .27 | — | 41 | 44/47 | 20 / 20 / 17 |
| Union-find on shared words | .25 / .72 | many | 27 | 44/47 | chains unrelated stories |
| Event-type merge, no shared word | .50 / .56 | 5 (one high-tier) | 24 | 44/47 | 20 / 8 / 18 |
| Local embeddings (model2vec) | .45 / .59 | 4 (two high-tier) | 22 | 44/47 | 20 / 15 / 19 |
| LLM grouping (Haiku), tuned prompt | .61 / .53 | 2–4 (one high-tier) | 11–13 | 44/47 | 19 / 6 / 22 |
| Merge pass, cosine 0.15 | .55 / .48 | 0 | 26 | 44/47 | 21 / 9 / 18 |
| Merge pass, cosine 0.25 | .64 / .42 | 0 | 32 | 44/47 | 19 / 12 / 18 |
| Merge pass, cosine 0.20 (as designed) | .65 / .53 | 0 | 28 | 44/47 | 21 / 11 / 18 |
| **Merge pass, tightened (the review's variant)** | **.65 / .53** | **0** | **28** | **44/47** | **21 / 12 / 18** |

"0 hidden" means no event that v7 showed on the 24 labelled names is hidden; one apparent loss is
an article carrying two labels for one story. It cannot see paths no labelled case exercises:
the review found three (below) by constructing them. **The rules and thresholds were chosen on
these 32 names; there is no holdout.** Cosine 0.20 sits on a narrow peak of pair recall; 0.15
removes slightly more duplicates but merges more distinct events (pair precision .55), which is
why 0.20 was kept. A wrong merge hides one headline in a display-only section, never a score.

## The shipped design

After the seed clustering, one pass visits clusters oldest first. A cluster joins the earliest
cluster that has not itself joined another, when both are or neither is a reversal (a denial,
withdrawal, rejection or block), and any of these holds:

- they start within 4 days and their TF-IDF wording is similar (cosine at least 0.20);
- they start within 4 days and both are results stories, or both are layoff stories (a company
  has each at most once per window);
- both name a deal, they share a capitalized word (the counterparty), and they start within
  7 days. Weekdays, months, exchange and wire names and the deal words themselves never count
  as the shared word, and "to buy" counts as a deal only after a deal verb ("plans to buy"),
  never in a rating ("upgrades to Buy").

Trial or study data and dividends never count as "results", and "workforce" counts as a layoff
only after a reduction verb: each rides along with earnings or hiring news but is its own event.

Every test reads the clusters' original wording and words, so a merge reaches one hop: a story
cannot snowball. Ties in article time break on the article id, so the section does not depend on
feed order. Cost: no new dependency, about 75 lines, about 3 ms per brief.

## Why not the LLM

It removed the most duplicates, but:

- 5 or 6 of 32 briefs changed between two runs of the same prompt;
- it hid 1 to 4 labelled events;
- each brief waited a median of 7 s (16 s worst);
- the experiment hit the account's monthly spend limit and 90 calls failed, so in production the
  section would change shape whenever the quota ran out.

Experiment spend was $0.12. Revisit only if duplicates still fail a panel after this ships.

## Review

Three reviewers read the PR before merge (correctness, simplicity, solution). The merge
mechanics held: one hop, no member lost or doubled, no crash on empty or non-ASCII input. Three
constructed paths hid a real event and were closed before shipping, each with a test: a trial
readout or a dividend raise folded into an earnings item; a second deal folded into the first on
a shared weekday; an acquisition folded into a "to Buy" rating note. The cost on the acceptance
names is one more duplicate in the harness (21 / 12 / 18), and most of CMG's improvement,
whose remaining merges had fired on generic words such as "takeover". The shipped code narrows
"to buy" a little further than the review's variant; its own numbers are the v8 row below and
the acceptance count under Known limits.

## Guard and the precision figure

High-tier recall stays at 44 of 47. The probe's precision figure falls from 130/176 (74%) to
117/165 (71%), because it counts a second item about an event as a hit. The probe now also
prints distinct labelled events shown and duplicate items:

| Filter | Precision | High-tier recall | Distinct events | Duplicate items |
|---|---|---|---|---|
| v7 | 130/176 | 44/47 | 109 | 53 |
| v8 | 117/165 | 44/47 | **112** | **42** |

The precision drop is accepted explicitly: it is duplicates leaving, not events.

## Known limits

- **A long story told from many angles still leaks.** On the acceptance names v8 shows 51
  items with 13 duplicates against v7's 57 with 20 (`scripts/dedupe_2026-10-18/accept_score.py`,
  counted from the hand tags), but CMG keeps 7 duplicates in 9 items. TF-IDF gives a story's own
  words little weight when it dominates the feed, opinion pieces carry no deal word, and some
  coverage is more than 7 days apart. Closing that lexically needs chaining or deal merging on
  generic words, and both hid events.
- **Two different acquisitions within 7 days that share a non-generic word** ("Beta Medical",
  "Gamma Medical") still merge. No labelled case has it.
- **Two different results-type stories within 4 days merge.** That is acceptable for an event
  a company has once a quarter.
- **Articles about other companies that only mention this one** stay out of scope. Every attempt
  to flag them, the LLM's included, also dropped a real high-tier event.

Production matches the frozen `scripts/news_filter_v8.py` on all 24 labelled names. On the 40
saved feeds it matches the prototype on 38. The two differences are same-timestamp articles,
which now sort by id instead of feed order.
