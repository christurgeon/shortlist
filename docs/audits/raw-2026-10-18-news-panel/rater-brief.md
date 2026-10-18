# Rater brief — recent-news items in stock research briefs

You are one of several independent raters. Work alone. Use no tools other than reading your
packet and writing your answer file. Do not search the web. Do not dispatch subagents.

## Context
An investor's research tool writes a filing-grounded brief per stock (10-K, 10-Q, 8-Ks). Under
each brief it prints up to 10 recent third-party news headlines from the last 30 days, chosen by
an automatic filter. The investor reads the brief to decide what to dig into next. Your job:
judge each news item and each ticker's news section, using the rubric below exactly.

Your packet lists 8 tickers. For each: "What the brief already says" (the brief's own content),
then the news items shown, each with an ID, date, outlet, report count, headline and summary.

## Item mark: WANT or NOT
WANT: the item reports a specific, material, company-specific fact — an event, action, result or
statement — that an analyst reading this brief would want in front of them. Examples: a deal or
contract, a regulatory or legal action, a clinical or product result, reported results or
guidance, a financing, a management change, a material customer or supplier development.
NOT: opinion, valuation or "is it a buy" columns; analyst rating, rank or price-target notes that
report no new company fact; previews of an upcoming event; stock-move recaps with no stated
cause; listicles and trivia; an earnings-date notice. If two or more items in one section cover
the same event, mark WANT only the single most informative one and NOT for the rest.

## Section mark: USEFUL, NEUTRAL or NOISE
USEFUL: at least one WANT item reports something material that the brief does not already
contain, and it would change what an analyst checks next. NEUTRAL: WANT items exist, but they
only repeat the brief or are minor. NOISE: no WANT item.

## Output
Write ONLY valid JSON to the answer file named in your dispatch, in exactly this shape:
```json
{"items": {"AXON-1": {"mark": "WANT", "why": "≤15 words"}, "...": {}},
 "sections": {"AXON": {"mark": "USEFUL", "why": "≤25 words"}, "...": {}}}
```
Every item ID in the packet must appear exactly once; all 8 tickers must appear in "sections".
Then return one line: "done, N items, M sections".
