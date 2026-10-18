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

| Option | Pair recall | Hidden events | Duplicate items (24) | High-tier recall | Acceptance: events / duplicates / junk |
|---|---|---|---|---|---|
| v7 (status quo) | .27 | — | 41 | 44/47 | 20 / 20 / 17 |
| Union-find on shared words | .72 | many | 27 | 44/47 | chains unrelated stories |
| Event-type merge, no shared word | .56 | 5 (one high-tier) | 24 | 44/47 | 20 / 8 / 18 |
| Local embeddings (model2vec) | .59 | 4 (two high-tier) | 22 | 44/47 | 20 / 15 / 19 |
| LLM grouping (Haiku), tuned prompt | .53 | 2–4 (one high-tier) | 11–13 | 44/47 | 19 / 6 / 22 |
| **Merge pass (shipped)** | **.53** | **0** | **28** | **44/47** | **21 / 11 / 18** |

The merge pass hides no labelled event. One apparent loss is an article that carries two labels
for one story.

## The shipped design

After the seed clustering, one pass visits clusters oldest first. A cluster joins the earliest
cluster that has not itself joined another, when both are or neither is a reversal (a denial,
withdrawal, rejection or block), and any of these holds:

- they start within 4 days and their TF-IDF wording is similar (cosine at least 0.20);
- they start within 4 days and both are results stories, or both are layoff stories (a company
  has each at most once per window);
- both name a deal, they share a capitalized word (the counterparty), and they start within
  7 days.

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

## Guard and the precision figure

High-tier recall stays at 44 of 47. The probe's precision figure falls from 130/176 (74%) to
116/165 (70%), because it counts a second item about an event as a hit. The probe now also
prints distinct labelled events shown and duplicate items:

| Filter | Precision | High-tier recall | Distinct events | Duplicate items |
|---|---|---|---|---|
| v7 | 130/176 | 44/47 | 109 | 53 |
| v8 | 116/165 | 44/47 | **112** | **41** |

The precision drop is accepted explicitly: it is duplicates leaving, not events.

## Known limits

- **A long story told from many angles still leaks.** CMG drops from 10 items with 8
  duplicates to 8 with 5. Opinion pieces without a deal word, and coverage more than 7 days
  apart, do not merge. Closing that lexically needs chaining or deal merging without a shared
  word, and both hid events in testing.
- **Two different results-type stories within 4 days merge.** That is acceptable for an event
  a company has once a quarter.
- **Articles about other companies that only mention this one** stay out of scope. Every attempt
  to flag them, the LLM's included, also dropped a real high-tier event.

Production matches the frozen `scripts/news_filter_v8.py` on all 24 labelled names. On the 40
saved feeds it matches the prototype on 38. The two differences are same-timestamp articles,
which now sort by id instead of feed order.
