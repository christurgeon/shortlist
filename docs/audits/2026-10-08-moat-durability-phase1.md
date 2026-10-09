# Moat durability, Phase 1 — the ROIC-persistence section (2026-10-08)

**What this note is.** The record of how the verdict of `2026-10-04-moat-durability-verdict.md`
became a section of the `/deep` brief: what was built, what was decided and at what cost, and
the evidence that the section is computed on the basis the study measured. It is evidence as
of its date. For how the code behaves today, read `docs/RESEARCH.md`.

**Status: built 2026-10-08 and 2026-10-09, off.** `research.durability.enabled` is `false`. Nothing changes in a brief
until it is turned on.

## What was built

With the flag on, every brief carries one deterministic section, "ROIC persistence", in the
markdown brief and in the Telegram HTML report.

- For a company whose ROIC is in the top fifth of the study's universe: the historical
  frequency with which such a ROIC lasted three years, and the company's third among top-fifth
  firms on the two predictors that passed (growth of invested capital; steadiness of the ROIC
  rank), each with the caveats the verdict attaches to it.
- For every other company: one sentence, "Not shown: …", with a reason from a closed set of
  thirteen.

It is computed after the model call. The model never sees it.

## Decisions, each with its cost if wrong

1. **Display only. The model does not receive the line.** The design written before the
   verdict put the line in the prompt with a required `reconciliation` entry. A three-lens
   review of the Phase 1 design showed that the prompt path's risk is misuse and not neglect:
   a filing's account of its competitive position will nearly always "agree" with a
   favourable reading, and a verified `confirms` entry then reads as a confirmed moat. It also
   touches the entry cap (`max_conflicts`), the fabrication counter (`unverified_count`), the
   conviction guards, and the Telegram reconciliation rows. Only a paid A/B can measure
   misuse. A deterministic section needs none of that and no A/B.
   *Cost if wrong:* the model does not reconcile the reading against the filing; the reader
   does.
2. **Measured content only.** The "years of excess return the price pays for" pairing of the
   old design is dropped: it is an unvalidated reframing with a hurdle-rate assumption.
   *Cost:* no price pairing.
3. **A third, never a probability.** The effect is a straight-line fit within SIC-2 sector and
   cohort year. A per-company number would extrapolate it. The section gives the company's
   third among all top-fifth firms, prints where the thirds split, and says the thirds are
   across sectors while the effect is within one. The effect itself is said as what the
   coefficient is: the gap between the two extreme firms of the fit, with its 95% interval,
   "not a gap between thirds".
4. **The heading is "ROIC persistence".** The verdict: "a claim about an accounting ratio",
   and of `investment`, "NOT yet a finding about businesses". The third labels are neutral
   ("slowest-growing", "steadiest"), never "favourable".
5. **A company the study does not cover is told so.** Silence has a dozen causes the reader
   cannot tell apart, and stderr never reaches Telegram.
6. **`providers/_xbrl_facts.py` is now a read-only dependency of a production path.** Verdict
   Addendum 1 said this "must be fixed" before a `/deep` import. It is fixed by deciding it:
   the section must read `annual_series` to be on the measured basis, the file is not edited,
   and `CLAUDE.md` states the exception. *Cost:* a later edit to `annual_series` moves a
   displayed number; the equivalence test below then fails.
7. **The reference year is fiscal 2025**, one past the years the Phase 0 gates covered. See
   "The reference year" below.

## The shared basis moved, and nothing else did

The form filter, the compaction and the arithmetic of the two passing predictors moved from the
study modules into `src/shortlist/durability.py`, so that the study and the section call the
same code. This closes prerequisite 1 of the final review: the 10-K form filter is now in front
of every reader, and a 20-F row in raw company facts is no longer read.

**The code digest changed.** `probe_durability.py: CODE` names `durability.py`,
`durability_data.py`, `durability_study.py` and `config.yaml`, all edited here. The raw outputs
carry `code_sha256` `f7780551…16b6d9`, and the code no longer has that digest. (No new value
is given here: `config.yaml` is in `CODE`, so any later config edit moves it again.) Three
stale comments in the study code, left alone in Phase 0 only to keep the digest, were corrected
in the same change. No study step was re-run. The table loader, the profile and the section are deliberately in
modules outside `CODE` (`durability_profile.py`, `research/durability.py`), so later edits to
them do not move the digest again.

**Evidence that no number moved.** `scripts/probe_durability_equivalence.py` digests, from the
committed data file:

| what | rows | SHA-256 |
|---|---|---|
| every `YearRow` of every snapshot 2011–2025 of 12,320 firms | 608,419 | `2e1d4b02…604dcc7` |
| every cohort row, start years 2011–2021 (state, outcomes, controls, all six predictors and their ranks) | 3,771 | `6ddb7acf…d282c5cb` |
| the table inputs at fiscal 2025 (four universes, their floors, the predictors of each top-fifth member) | 361 | `6a83d50b…66cbac73` |

The script was committed and run on a clean tree **before** the refactor, and its output is
`raw-2026-10-04-durability/equivalence.json`. In history these are the two commits that precede
"refactor(durability): one shared basis for the study and the /deep section". At both of them
the digest of the `CODE` files is `f7780551…16b6d9`, the one the raw outputs name: the digests
were computed on the study's own code. (`equivalence.json: code_commit` names the hash that
commit had before the branch was rebased; cite it by its title.)
`tests/test_durability_equivalence.py` recomputes all three on the current code in every test
run. The cohort row count is 2,290 + 1,481, as in the verdict, and the universe sizes for
2011–2024 equal `gates.json`.

What the digests do not reach, and what covers it:

- `comparison_panel` / `comparison_count` (the reproduction gate's population): a test
  re-derives `gates.json: comparison_counts` and `facts_filed_before_period_end`.
- The tag list `KEEP_TAGS` (a tag that was never kept cannot appear in a digest of the
  compacted file): pinned literally in `tests/test_durability.py`.
- The form filter (the compacted file has no foreign row to drop): a unit test on raw facts.

A fresh reviewer ran sixteen deliberate mutations of the moved code against these digests (on
scratch copies; a record of the review, not reproducible evidence).
Ten changed a digest, two crashed, two were caught by a unit test only (the form filter, a
missing floor), and two survived: a tag dropped from `KEEP_TAGS`, and the year-gap constant.
Both now have a test.

**The live path equals the study path, end to end.** For every firm of the fiscal-2025 top
fifth whose latest year on the download date is 2025, the profile computed by the live code
(`compact_facts` → `durability_profile.profile`) has the ROIC, the capital growth and the rank
spread that the study path (`build_firm` → `snapshot` → `_predictors`) gives, to the last bit.
Every masked firm and every firm below the floor gets its reason.

**What only a live request can show.** That the per-CIK company-facts API returns the rows of
the bulk archive, in the same order (when two rows for one period end have the same filed
date, the first row wins). Run once on 2026-10-08
(`tests/test_durability_live.py`, `pytest -m live`):

| company | rows filed on or before 2026-10-07 | result |
|---|---|---|
| Apple (CIK 320193) | 556 | identical, in order, to the study's file |
| Exxon Mobil (CIK 34088) | 214 | identical, in order |
| Microsoft (CIK 789019) | 612 | identical, in order |

Three firms, one day. It is a check of the mechanism, not of every filer.

## The reference year

The section ranks a company against `src/shortlist/durability_table.json`: the ROIC universe of
fiscal 2022–2025 as seen in each firm's fiscal-2025 snapshot, the top-fifth floors, and the
predictor values of the 357 firms in the 2025 top fifth (348 with a capital growth, 341 with a
rank spread). The measured effects in the same file are copied from `discovery.json`,
`holdout.json` and `decomposition.json`; the builder (`scripts/build_durability_table.py`)
never estimates one, and a test rebuilds the file byte for byte.

**Fiscal 2025 is outside the reproduction gate**, which had SEC `frames` targets for 2011–2024.
With fiscal 2024 as the reference year the section would already abstain for companies with
July–September 2026 year ends, and for most companies from February 2027. Why 2025 is usable,
from the committed data (the table's `completeness` block; a test holds each line):

| | fiscal 2024 | fiscal 2025 |
|---|---|---|
| universe (ROIC defined, revenue ≥ $100M, not masked) | 1,812 | 1,787 |
| share of the prior year's universe with a usable row | 92.1% | 92.5% |
| top-fifth floor | 15.75% | 15.53% |

If all 25 firms of the shortfall were below every firm in the 2025 universe, the floor would be
15.47%; if all were above, 15.91% (`completeness.floor_bounds`). Residual: a firm with a June
2026 year end had its as-of date after the download (2026-10-07), so one that filed late in
that window is missing.

A company one fiscal year past the table is ranked against fiscal 2025 and the section says so.
Two years past, it is not shown. The table lasts until fiscal-2027 10-Ks, from about September
2027.

**A refresh is not built. Do not run `probe_durability.py fetch` in this tree to make one:** it
overwrites `sic.json` and `fetch.json` of the study, whose hashes the raw outputs name, and the
builder would still read the committed 2026-10-07 data file and a hard-coded table year. A
refresh needs a builder that takes its own data directory and its own table year. When it is
built, it cannot change a measured effect: those are copied from the study's outputs.

## Three guards that the study does not have

The study never met these cases; the live path does.

- **An unknown SIC is not shown.** In the study 12,523 of 12,539 filers had a SIC and an unknown
  one was simply not masked. Live, the SIC comes from one fetch that can fail, and a bank with
  no SIC would get a section.
- **Capital growth is read only across year ends 350 to 380 days apart.** A fiscal-year change
  puts two year ends in one bucket, and "the year before" can then be two years back. In the
  reference cohort the gaps run from 364 to 371 days, so the guard changes nothing a company is
  ranked against.
- **The facts must hold the year the brief was written from.** When the brief's 10-K was filed
  more than 300 days after the latest year end in the facts, the facts are fetched once more
  past the day cache; if they still lag, the section says so.

**A company whose latest year on file is before the table's year is not shown**, and is told
that the section needs its newer 10-K. This is a company in the weeks before it files. The
table does hold a list for fiscal 2024, but it is 2024 as seen in the fiscal-2025 snapshot,
without the firms that left in between: 1,766 firms and a cutoff of 15.92%, against the
study's own fiscal-2024 universe of 1,812 firms and 15.75%. Shown against it, the section would
name a population that is not the study's, and the thirds would still come from the 2025
cohort. (An earlier fix on this branch did show it; the final review caught that.)

## What the section can and cannot fail

- One request to `data.sec.gov` per brief, through the process-wide throttle, under a size cap.
  The body is checked (a dict, the right CIK) before it is used, and the compacted record is
  cached for the day only after the profile has read it.
- **The deadline is a thread join, because an httpx timeout is not a deadline.** It applies per
  phase and per read. A reviewer served a response from a loopback socket with a 1 s timeout:
  a server that dripped the body one byte at a time ended at 1.8 s, and one that dripped the
  HEADERS ran for 79 s, stopped only by the header size limit. (A one-off measurement on the
  reviewer's scratch scripts; the test that holds the behaviour is
  `test_the_deadline_bounds_the_whole_request_whatever_the_server_does`.) The request now runs
  in a daemon thread and the caller waits `deadline_s` (15 s) for it. An abandoned thread is not
  stopped; it ends when the server ends the response or a read times out. Past
  `research_phase_budget_s` every brief of a run is lost, which is why an optional section
  must not be able to wait.
- `fetch_section` never raises. Malformed company facts make `annual_series` raise; that costs
  the section and never the brief.
- `research/assess.py` is not edited. Both prompts are byte-identical with the flag on or off.
  The section is not a grounding segment.
- The config hash and the prompt fingerprint both changed, so every cached brief key was
  invalidated once by this change.

## Reviews

- **Design, before any code** (three fresh Sonnet reviewers: an implementation fact-check, a
  signal-value skeptic, a red team). They changed the design in these ways: the section became
  display-only (decision 1); the heading lost "moat"; the population sentence was corrected
  (non-financial 10-K filers with revenue of $100M or more, not "US filers"); the base rate
  gained its denominator (firms with no usable annual data three years later are 16% and 11%
  of the cohorts and are not counted); the effect sentence gained its intervals and "on a
  straight-line fit"; the `stability` line gained the by-year figures; "not shown" became
  visible; the three guards above were added; the fetch gained a deadline, a body check and a
  compacted cache; the reference year moved to 2025.
- **The shared basis and the table** (one fresh Sonnet reviewer, with the mutation run above).
  No behaviour change found. Its findings moved the table loader and the profile out of the
  study's code digest into `durability_profile.py`, tightened the loader, pinned the tag list
  and the reproduction-gate counts, and made the reference-year argument reproducible.
- **The profile, the section and the wiring.** The first reviewer stalled and returned nothing.
  It was replaced by a scripted mutation pass and two narrower fresh Sonnet reviews.
  - *Mutations:* 84 deliberate bugs in the profile, the section, the fetch, the wiring, the
    cache key and the Telegram rendering, in three passes as the code changed. 79 turned a
    test red at once; each of the other five now has a test (a late 10-K replaced by an older
    year end in the same bucket; a tie exactly on a third boundary, twice; the cutoff year
    named for a below-cutoff company; a config block of the wrong type). The mutation scripts
    were scratch files and are not committed, so these counts are a record of the review and
    not reproducible evidence; the tests they led to are.
  - *Failure paths:* the deadline, the pre-10-K case, the cut points and the cache order above
    all come from this review, with a closed stderr and an orphan temp file.
  - *Wording against the verdict:* every printed number matched. The sentences changed: the
    effect is the gap between the two extreme firms; one unit per number (percentage points
    for an effect, percentile points for a rank spread); `stability` has no 2011 cohort, so its
    first window is 2012–2017; the base rate names who is left out and that under 4% of the
    counted firms are held on a positive operating income with too little capital for a ROIC;
    the capital split names its population and that it is descriptive; "eleven tests on six
    predictors" and "not an independent sample"; "not evidence of a separate trait"; payouts
    among the untested causes; no cause named for low capital; a data failure is never told
    "the study does not cover it"; the four by-cohort-year figures of `stability` were removed
    (the +62 is what a hurried reader would keep) and the sentence that the result rests on
    2020 and 2021 stays.
- **The whole branch** (one fresh Opus reviewer): "merge-ready after fixes". The code was safe
  with the flag off and the study stood. What it found, all fixed here: the documented table
  refresh was wrong and would have overwritten two evidence files of the study; the
  before-the-new-10-K case named a population that is not the study's (now not shown); two
  cutoff figures in this note were inconsistent and one was held by no test; the last line of
  the section read as "two passed, then were checked" when the pass rule includes the later
  cohorts and a third test cleared the first cohorts and failed there; a below-cutoff sentence
  could print the same number twice.

## For a Phase 2 that gives the line to the model

Not built. If it is ever proposed, these are the findings to start from.

- **The metric is misuse, not use.** With a "REQUIRED" clause the model will use the line
  (the 2026-08-25 options precedent went 3 of 3). The question is whether the thesis, the bull
  case or the call cites the line as support for a moat, quality, pricing power or a return,
  and whether the stance or the model's own conviction moves. Any misuse in three briefs means
  the line stays out.
- **Point the model at what was not tested.** Ask it to name, in the filing's own words, what
  changed invested capital in the last year (acquisitions, buybacks, dividends, impairments,
  debt, retained cash, a one-year profit change), and to say "silent" when the filing does not.
  Do not ask it to compare the line with the filing's competitive position.
- **Mechanics to settle first:** a `durability` reconciliation entry can be cut by
  `max_conflicts`; an unmatched quote on it increments `unverified_count`, the reader's
  fabrication signal; a verified `confirms` on it would count toward HIGH conviction in
  `_high_corroborated`, and the same reading filed under `moat` would too; the Telegram report
  shows reconciliation rows with no line beside them.
- n = 3 names decides only a lopsided result.

## Limits

- A fiscal-2026 company is ranked against fiscal-2025 firms. 41 of the 357 firms in the 2025 top
  fifth are within one point above the cutoff, and 33 more are within one point below it. The
  section says when a company is within one point.
- The table's cutoffs for fiscal 2022 to 2024 (18.9%, 16.6%, 15.9%) are those years as seen in
  the fiscal-2025 snapshot and are higher than each year's own cutoff, because the lists omit
  the firms that left before 2025 (fiscal 2024 in its own snapshot: 15.75%,
  `completeness.floor_2024_own`). They are used only to rank a company's own earlier years for
  the steadiness measure, as the study does for a cohort's history.
- A brief is cached for the day with the section it was written with. A section that failed
  ("SEC data could not be read", "does not yet include the latest 10-K") stays in that brief
  until the day bucket turns or the brief is refreshed, as for the proxy and options lines.
- A stalled download is abandoned after `deadline_s`, not stopped: its thread holds one socket
  until the server ends the response or a read times out. A brief makes at most two requests.
- The thirds are positions across all top-fifth firms; the effects are within a sector. One
  sector can sit mostly in one third.
- Before the year end plus 120 days the profile reads what is on file today, which is less
  than the study read at 120 days if an amendment arrives in between.
- A company that does not tag operating income, equity, total assets or revenue under the tags
  the study reads is not shown. Exxon Mobil is one: its record in the committed data file has
  no `OperatingIncomeLoss` tag.
- Zero tagged debt reads as no debt, as in the study (39% to 51% of each discovery cohort, per
  the verdict). The section says so per company.
- Current SIC decides the sector mask, as in the study.
- The section is long: about 3,100 characters for a company with both predictors. Each clause
  is one the verdict requires or a reviewer showed to be misread without it. It was not
  shortened.
- The reader can still over-read the section. Its heading, its first sentence and the caveat in
  each bullet are the only guard.

## Not done

- The flag is off. Turning it on is the maintainer's decision.
- The prompt integration (Phase 2 above).
- A table refresh on newer SEC data.
