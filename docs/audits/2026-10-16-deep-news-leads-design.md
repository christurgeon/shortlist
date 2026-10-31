# Recent news in `/deep` — decisions (2026-10-16 to 2026-10-26)

How the section behaves: `docs/RESEARCH.md` → the recent-news section. This file keeps what was
decided and what was tried and rejected, so none of it is re-tried from scratch. The probe
scripts, labels, frozen filter versions and rater-panel files are deleted; they are in git
history.

## Decisions

- **Display only.** The section is computed after the model call. The model never sees it, and
  the prompt, cache key, score, gates, flags and screening call are unchanged by it.
- **Feed: Finnhub `company-news`.** Reuters, Bloomberg, WSJ, FT and AP have no free API; Google
  News RSS names the publisher but has no summary; FMP is the quota bottleneck. Finnhub labels
  most items "Yahoo" (syndication), so the original outlet is mostly unrecoverable and the
  section says it is not verified. The free feed returns only the newest ~250 articles.
- **A deterministic filter** (`research/news.py`). The headline must name the company, because
  summaries mention every company in a story. A broker rating change is kept: the labelled event
  sets count it as an event. Articles about one event show as one item: seed clustering on
  shared words, then one merge pass that cannot chain.
- **Merge threshold: TF-IDF cosine 0.20.** Lower merged distinct events; higher left more
  duplicates.

## Tried and rejected

- **LLM grouping to remove duplicates.** It removed the most duplicates but changed 5 or 6 of 32
  briefs between two identical runs, hid real events, added a median 7 s per brief, and fails
  whenever the monthly API quota runs out.
- **Local embeddings and union-find chaining on shared words.** Both hid more real events than
  the merge pass; chaining joined unrelated stories.
- **Flagging articles that only mention the company in passing.** Every rule tried, the LLM
  included, also dropped a real high-tier event.
- **Word-anchoring event terms** (`trial` inside "Industrial"). The freed slots went to other
  non-events.
- **Demoting analyst rating notes.** They are the most common unwanted item, but also the most
  common labelled event; demoting them lost real events.
- **A shorter list.** Wanted items were spread evenly over display ranks, so cutting the list
  removed them as fast as noise.
- **Feeding the news to the model.** A spike worked mechanically, but the model marked almost
  every item "silent" against the filings, and it brings unattributed claims and prompt
  injection through summaries. Build it only with evidence that it improves the brief.

## Known limits

- Articles about other companies that name this one in passing still appear.
- A story told over more than a week, or in opinion pieces, still repeats.
- A company name that is a common word (Target, Gap, Snap) matches ordinary headlines.
- A brand that differs from the legal name ("onsemi") does not match.
