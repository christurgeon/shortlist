# Exposure map in `/deep` — design, 15-filing probe and live check

**Date:** 2026-11-02 · **Status:** design, implemented in the same PR · **Scope:**
`shortlist.research` and the bot report only. No scoring, gate, flag or composite change.
Origin: item 2 of the `/deep` context plan (geography, customers, suppliers, government).

## 1. The problem

Before this change, `/deep` saw a company's geographic, customer and government dependence only
where Items 1, 1A and 7 describe it, which is usually in words. The figures ("Greater China is
X% of net sales", "two customers are 34% of revenue", "the U.S. Government is 71% of sales")
are in the 10-K's segment, geographic, revenue and concentration notes, and those notes were
not in the bundle. Such a figure is what makes a tariff, an export rule or a budget fight
material to one name.

## 2. What was built

- **A second note family** in `research/notes.py`. A family is a list of title regexes in
  priority order, an exclude regex and a budget. The debt family is unchanged (its tests pass
  unmodified). The exposure family reads the 10-K only.
  - Title tiers, in priority: `segment|geographic`; `concentration|major customer|significant
    customer`; `revenue|net sales`. Within a tier, document order.
  - Excluded: `deferred`, `unearned`, `contract asset/liabilit`, `income tax`, and every title
    the debt family's title regex matches, so no note reaches the prompt twice.
  - Budget (`research.exposure_notes`): 3 notes, 12,000 chars per note, 16,000 per 10-K.
  - Each note is a labelled haystack segment (`10-K note: SEGMENT INFORMATION`) with the
    same `=== 10-K STATEMENT NOTE — … ===` header as a debt note. Truncation is in the
    header only.
- **A brief field `exposures`**: a list of `{kind, claim, evidence}`, `kind` one of
  geography, customer, supplier, government, commodity, currency, regulation. Verified like
  `risks` (an empty or unlocatable quote is unverified and counts in `unverified_count`),
  with `source` recording the segment. `research.max_exposures` (default 5) is a ceiling.
- **Prompt.** One `EXPOSURES` paragraph in `SYSTEM_PROMPT` names the field (the
  destination-field rule, `2026-08-25-options-line-destination-verdict.md`). `exposures`
  joins the verbatim-quote rule, the one-quote-one-item rule and the "empty evidence is never
  valid" rule. A derived share goes in `claim` with its inputs, never in `evidence`. A table
  row is acceptable evidence only when copied exactly, `|` separators included, because that
  is how `Note.to_markdown()` renders tables. The example figures in the paragraph are
  fictional, so they cannot leak into a real brief.
- **Rendering.** "Exposures (filing-grounded)" in the markdown brief, after "Material risks";
  "Exposures" in the Telegram HTML card after "Risks", and 🌐 lines in the FULL text report.
  The kind leads each claim. A record without the field loads with an empty list.

## 3. Decisions

| Decision | Choice | Why |
|---|---|---|
| Separate config block or reuse `research.notes` | Sibling block `research.exposure_notes` | Different budget, 10-K only, and disabling one family must not disable the other. |
| Selection order | Priority tiers, not document order | AAPL files `Revenue` before its segment note. In document order a long revenue note could take the budget the segment note needs. |
| Content gate on revenue notes (keep only notes with geography) | Not built | Measured unreliable (§4.2): a row regex missed DE's and GD's real disclosures. The revenue tier is last, so a low-value revenue note only uses budget the higher tiers left. |
| Unknown `kind` | Dropped at parse time | The same rule as an unresolved reconciliation `signal`. Case and a few aliases (`customers`, `fx`, `regulatory`, …) fold first. |
| Quote reuse between `risks` and `exposures` | Prompt rule only, not enforced in code | The register's D6 entry: quote reuse runs at about one per three briefs and was deliberately left as a prompt rule. Measured in §5. |
| Fully disabling the field | Not built | Like every other list field, the schema and instruction are unconditional. `exposure_notes.enabled: false` removes the note sections, which leaves the user prompt byte-identical to a bundle without them (tested); `max_exposures` always prints in the ceiling line. |

## 4. Probe (2026-11-02, oracle-prod)

`docs/audits/scripts/probe_exposure_notes.py`, one ticker per process, latest 10-K.

### 4.1 Selection

Payload flags are regex hits on the selected text, a coarse indication only: geography
(region and country names), customer share ("customers accounted for", "% of net revenue"),
government (U.S. Government, DoD, Medicare, Medicaid), commodity, currency.

| Ticker | Selected notes (kept chars / full chars) | Payload flags | Missed or notable |
|---|---|---|---|
| AAPL | Segment Information and Geographic Data (5,257); Commitments, Contingencies and Supply Concentrations (3,485); Revenue (6,062) | geography; supply concentration | `Financial Instruments` (receivables concentration) not selected |
| AMAT | Industry Segment Operations (9,347) | geography, customer share (19% and 15%) | — |
| CROX | OPERATING SEGMENTS AND GEOGRAPHIC INFORMATION (7,328); REVENUES (2,738) | geography, currency | — |
| DE | SEGMENT DATA (5,498); REVENUE RECOGNITION (2,787) | geography in narrative only | edgartools renders no table for DE's revenue note: "by primary geographic market … follow:" is followed by nothing |
| F | SEGMENT INFORMATION (11,998 of 22,505, cut); REVENUE (3,995 of 10,414, cut) | geography | REVENUE is a by-source table with no geography: low value |
| GD | Segment Information (5,076); Revenue (10,920 of 13,433, cut) | geography, government ("Total U.S. government … 35,757" of 52,550) | — |
| HDSN | Segment information (1,528) | none | single segment; customer concentration sits in the 28,672-char accounting-policies note, not selected |
| JPM | Business Segments & Corporate (11,991 of 39,865, cut); Credit Risk Concentrations (4,003 of 13,535, cut) | geography, currency | a bank: low value expected |
| KO | OPERATING SEGMENTS (10,106); NET OPERATING REVENUES (5,884 of 7,447, cut) | geography | — |
| LLY | Segment Information (3,476); Revenue (9,866) | geography (U.S., Europe, Japan, China), government (Medicare/Medicaid) | — |
| LMT | Information on Business Segments (11,998 of 17,404, cut) | government ("U.S. Government … $53,412" of $75,048 at char 4,421, inside the cut) | — |
| NKE | SEGMENT INFORMATION (9,441); REVENUES (4,270) | geography, currency | — |
| O | Segment and Geographic Information (4,643) | geography | a REIT |
| TXN | Description of Business, Including Segment and Geographic Area Information (7,135); Financial Instruments and Risk Concentration (2,494) | geography, customer share | — |
| XOM | Disclosures About Segments And Related Information (11,994 of 35,235, cut) | commodity | the geographic table is at chars 31–33K, past the cut |

All 15 filers have a segment note and it is selected first. Geography reaches the prompt for
13 of 15 (not HDSN, single segment; not XOM, cut). Customer concentration as a share reaches
it for AMAT and TXN, government as customer for GD, LMT and LLY.

### 4.2 Revenue notes without geography — measured, not gated

A row regex for geographic table rows (`| United States |`, `| China |`, …) found rows in
LLY's and CROX's revenue notes and none in AAPL's, F's, NKE's, KO's, DE's and GD's. Two of
those zeros are wrong: DE's table is not rendered (§4.1), and GD's government table renders
as plain text, not a markdown table. A content gate would have dropped GD's government data.
Titles stay the only selection rule.

### 4.3 Memory

Peak RSS (`ru_maxrss`) after the 10-K parse, after the debt family, and after the exposure
family. The debt family's first `.notes` access does the XBRL download and parse; the
exposure family reuses it.

| | min | max |
|---|---|---|
| after parse | 207 MB | 254 MB (JPM) |
| after debt notes | 256 MB | 537 MB (JPM) |
| marginal for exposure notes | 0 MB | 0 MB (all 15) |

## 5. Live check

Pre-set bar: the field is populated on all three tickers, at least two thirds of entries
verify, and no derived figure appears inside an `evidence` quote. One round each, the
`/deep` path (`require_passed=False`, `--refresh` semantics; AMAT is gated on
`heavy_insider_selling`, so the plain CLI skips it), `claude-sonnet-5-5`, one ticker at a
time. Every first attempt returned `ok`; no retry was needed. Cost $0.39 + $0.29 + $0.41 =
**$1.08**.

| Ticker | Exposures | Verified | Kinds | Verified against a new note |
|---|---|---|---|---|
| AMAT | 4 | 4 | customer, geography, regulation, supplier | 0 (the same sentences are also in Items 1A/7, and `_locate` attributes to the first segment that holds the quote) |
| AAPL | 4 | 4 | geography, supplier, commodity, customer | 2: a geographic table row and the supply-concentration sentence |
| LMT | 5 | 5 | government, customer, geography, supplier, commodity | 0 (same reason as AMAT) |

**Bar met: 3 of 3 populated, 13 of 13 verified, no derived figure in an `evidence` quote.**
Every derived share sits in a claim with its inputs, and each was checked by hand against the
filing figures (AMAT China $8,529M of $28,368M = 30%, Taiwan 24%, Korea 20%; AAPL Greater
China $64,377M of $416,161M = 15.5%; LMT international $21,343M of $75,048M = 28%). No
exposure quote repeats or overlaps a quote in `risks`, `red_flags`, `added_risks`,
`management_findings`, `moat.sources` or `reconciliation`.

The entries, as recorded:

- AMAT customer: "Two customers made up about 19% and 15% of FY25 net revenue (about 34%
  combined)." — *During fiscal 2025, two customers accounted for approximately 19% and 15%,
  respectively, of our net revenue.*
- AMAT geography: "About 89% of FY25 revenue came from outside the US. China was $8,529M of
  $28,368M (30%), Taiwan $6,857M (24%) and Korea $5,608M (20%)." — *In fiscal 2025,
  approximately 89% of our net revenue was to customers in regions outside the United
  States.*
- AMAT regulation: US export controls and China policy limit addressable sales — *Many of
  these challenges are present in China and Korea, markets that represent a significant
  portion of our business.*
- AMAT supplier: single or limited suppliers — *Accordingly, some key parts may be obtained
  from only a qualified single supplier or a limited group of qualified suppliers.*
- AAPL geography: "Greater China: $64,377M of $416,161M net sales = 15.5% …" — *| China (1) |
  64,377 | 66,952 | 72,559 |* (verified against `10-K note: Segment Information and
  Geographic Data`)
- AAPL supplier: hardware built by partners in Asia — *Substantially all of the Company's
  hardware products are manufactured by outsourcing partners that are located primarily in
  China mainland, India, Japan, South Korea, Taiwan and Vietnam.* (verified against
  `10-K note: Commitments, Contingencies and Supply Concentrations`)
- AAPL commodity: single or limited source components — *Because the Company currently
  obtains certain components from single or limited sources, the Company is subject to
  significant supply and pricing risks.*
- AAPL customer: 60% of net sales through indirect channels — *During 2025, the Company's net
  sales through its direct and indirect distribution channels accounted for 40% and 60%,
  respectively, of total net sales.*
- LMT government: "The U.S. Government was 72% of 2025 sales, and the Department of War was
  63% …" — *We derived 72% of our total consolidated sales from the U.S. Government in 2025,
  including 63% from the Department of War (DoW)*
- LMT customer: the F-35 is 27% of sales — *The F-35 program is our largest program,
  generating 27% of our total consolidated sales, as well as 67% of Aeronautics' sales in
  2025.*
- LMT geography: international 28% of sales, Europe about 12%, Asia Pacific about 10% —
  *In 2025, 28% of our total sales were associated with international customers.*
- LMT supplier: single or sole-sourced components — *a significant number of the components or
  supplies used are currently single or sole sourced*
- LMT commodity: rare earth minerals — *Some of our products require relatively scarce raw
  materials, such as rare earth minerals.*

What this does **not** show:

- **No A/B.** There is no no-notes arm, so this does not measure how much the notes changed
  the field. The direct evidence that the notes are used is AAPL's two note-only quotes.
  Most quoted sentences also occur in Items 1A and 7.
- **Kind assignment is loose.** AAPL's single-source components are tagged `commodity`, not
  `supplier`; AAPL's indirect channel share and LMT's F-35 program share are tagged
  `customer`. The kind is a display label and the claim carries the substance.
- **LMT filled the ceiling of 5.** Each entry is a distinct, quoted dependency, but a full
  list is what the materiality instruction warns about. Three tickers cannot show whether
  the ceiling is binding.

## 6. Known gaps

- A geographic table at the end of a very long segment note is cut (XOM). Raising the per-note
  cap to 36K for one filer was not judged worth the prompt cost.
- edgartools does not render every note table (DE's revenue note).
- Concentration filed only in the accounting-policies note is not selected (HDSN); that
  note is 28K chars of mostly unrelated policy text.
- Receivables concentration (AAPL's `Financial Instruments`) is not selected.
- Quote reuse between `risks` and `exposures` is a prompt rule, not a code check.
