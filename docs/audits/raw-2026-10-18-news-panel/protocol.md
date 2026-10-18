# §8 usefulness review — panel protocol (fixed 2026-10-18, before any panel rating)

## Why a panel
Design §8 (`docs/audits/2026-10-16-deep-news-leads-design.md`) specified the maintainer's own
marks. On 2026-10-18 the maintainer delegated the marking ("I don't want to have to basically do
the analysis on the news marks myself … use subagents to help decide"). A panel of three blind
raters replaces his marks. Everything below was fixed before any rater ran. The maintainer's
stated preference ("I really like the fact that we have it") is recorded but does not change the
rule. The controller's own earlier marks (19 strict / 28 loose of 56) were made after reading the
items and are excluded; they are reported only as a side note.

## Sample (unchanged from §8)
The 8 distinct tickers with a live /deep brief carrying the section, 2026-10-17: AXON, DECK, ELF,
JNJ, LMAT, MSFT, NVDA, UNH. 56 shown items. TGT was declared a known-gap probe before it ran and
is excluded.

## Raters
Three independent Sonnet subagents, identical rubric, blind to each other and to all prior marks.
Each gets a packet with a different ticker order and item order (seeded shuffle per rater), so
position cannot drive agreement. Each packet carries the brief's own content (takeaway, bull,
bear, risks, red flags, what would change my mind, business model, capital allocation) so a rater
can judge what the news adds beyond the brief.

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

## Aggregation and decision rule
- An item counts as WANT when at least 2 of 3 raters mark it WANT. A section counts as USEFUL
  when at least 2 of 3 mark it USEFUL.
- Kill rule, unchanged from §8: `research.news.enabled: false` if fewer than 40% of sections are
  USEFUL (3 or fewer of 8), or if fewer than half of all shown items are WANT (27 or fewer of 56).
  Otherwise the section stays.
- Reliability: report mean pairwise agreement and Fleiss' kappa for the item marks. If mean
  pairwise item agreement is below 70%, the verdict is reported as low-confidence.
- Recall (missed events) is not assessed: raters have no reliable knowledge of events after their
  training data, and §8 already called it a weak check.
