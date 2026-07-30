# 8-K 1.01∧3.03 composition audit + pre-production gates (2026-07-08)

> **Note added 2026-07-26 — verdict LABEL changed, evidence did NOT.** The evaluator's
> confidence intervals were rebuilt on an event-level bootstrap
> (`docs/audits/2026-07-26-funnel-composition-audit.md` §3a/§3b). On re-derivation the
> `edgar:8k` scored cohort's alpha CI is **[−10.21%, −7.15%] — still entirely negative**, and
> it *widened* under the honest bootstrap without approaching zero. This remains the
> strongest negative evidence of any cohort measured.
>
> Its formal verdict nonetheless moved KILL → INSUFFICIENT for an **unrelated and fragile**
> reason: a vintage-stratified measurability floor (2023 vintage 0.89 against a 0.90 floor;
> `n_measurable` drifted 400 → 396 on price re-fetch). **This is not a rehabilitation of the
> 8-K originator.** It stays `enabled: false`, and the substantive conclusions below stand.

**Registered protocol:** TODO.md "8-K originator/veto — operator smoke + composition audit
(pre-production) (2026-07-07)" (committed in #120) and design spec §5. **Implementation
plan:** adversarially reviewed twice pre-execution (methodology + mechanics, both
SOUND-WITH-FIXES, all fixes incorporated before any data was touched). This document is
committed for tamper-evidence; the tallies also live in the TODO entry per its own wording.

## Declared deviations from the registered wording
1. "Hand-classify" implemented as LLM-subagent classification with 30% independent blind
   double-coverage (raw agreement + Cohen's κ reported), blind third-agent tie-breaks, and
   mechanical-only `unreadable` semantics.
2. Label precedence (merger > rights_plan > reverse_split > credit_facility) is a plan
   decision (the registered text's ordering is a listing, not a precedence spec); an
   any-mention merger tally is reported so precedence cannot silently move the headline.

## Gate 1 — one-week live smoke (2026-06-22..28): PASS
- 8k leg: n_selected 4; 8k-neg: 31. Both `window_not_preregistered: true` + the NOT
  pre-registered warning (EXPECTED — smoke ≠ registered window). 100% immature
  (`fraction 0.00`, K=3m > elapsed) as designed. Idempotent re-runs: `written: 0` both legs.
- The 8k count sits below the registered ~15–25 band. Investigated under the
  fetch-bug-only rule: the raw cached EFTS rows for that week contain exactly 4 matched
  1.01∧3.03 filings (and exactly 31 negative-item rows = the 8k-neg count) — the selection
  function is faithful; the registered band was a miscalibrated pooled estimate. The
  direct-probe daily average (~1.75/day) matches the full-window enumeration (1.85/day).

## Gate 2 — items-vs-submissions fidelity (20 accessions): PASS
20/20 exact containment (event `meta.items` ⊆ submissions `items`), zero mismatches.
Sample: sha256 salt `fidelity-20260708`, 4 from the 8k leg (its full population), 16 from
8k-neg. Scope note (registered wording, inherited): containment catches EFTS
over-reporting only, not filings EFTS missed.

## Gate 3 — composition audit

### Frame (the shipped selection function over the registered window)
- Enumerated 2022-01-01..2025-12-31 via `fetch_eightk_window` (absolute cache dir
  `/home/chris/shortlist/.cache/efts` — the same cache the production runs will read).
- Selection: `assemble_eightk_events(rows, abstain, signal="edgar:8k")` — production's
  `_assemble_8k` defaults. Superset correction: live `company_tickers.json` resolution +
  shipped `_junk_suffix` post-filter removed **13** events (frame 1,877 → **1,864**).
  Residual (PiT-only-resolvable junk) quantified on the sample: **0/50** — no sampled
  event would be production-dropped.
- Per-year frame counts: 2022: 416, 2023: 507, 2024: 484, 2025: 457 (1.85/business-day —
  below the plan's 3–5 sanity band; same band-miscalibration as Gate 1, matches direct
  probes; investigated, no fetch bug).
- Frame sha256 `b485b8faccb27e3f256c1385b8a41db32a3b54c8e1e5298bb7a426995226c173`;
  sampling salt `audit-20260708` (per-accession sha256 rank, 13/13/12/12 per year).

### Calibration (out-of-window) + the one allowed revision
15 filings from 2026-06-08..07-07 (salt `calib-20260708`) classified under v1 definitions;
the pass surfaced 4 systematic ambiguities (de-SPAC/pubco `side` semantics, segment
carve-outs, Chapter-11 emergences, recurrent preferred issuances). One revision was
applied citing ONLY those cases (memo embedded in the frozen instructions file), then
definitions froze; the in-window 50 were classified exactly once under v2.

### Reliability
15/50 (salt `double-20260708`) independently re-classified blind: **raw agreement 14/15
(93.3%), Cohen's κ ≈ 0.90**. The one disagreement (0001095315-23-000045, PFSweb: merger
with a subordinate poison-pill carve-out amendment) went to a blind third agent —
2-of-3 majority: `merger_agreement` (the rights amendment existed solely to exempt the
acquirer). No orchestrator adjudication was needed anywhere.

### Tallies (n=50; per-year n≈12 ⇒ binomial 95% CI half-widths ≈ ±25–28%)
Per-year primary counts (merger / rights / reverse / credit / other):
- 2022 (n=13): 4 / 2 / 0 / 3 / 4 — merger share 0.31 (CI 0.06–0.56)
- 2023 (n=13): 4 / 3 / 1 / 1 / 4 — merger share 0.31 (CI 0.06–0.56)
- 2024 (n=12): 3 / 4 / 0 / 0 / 5 — merger share 0.25 (CI 0.01–0.49)
- 2025 (n=12): 4 / 0 / 0 / 1 / 7 — merger share 0.33 (CI 0.07–0.60)

**Volume-weighted pooled shares** (weights = exact per-year frame counts):
- merger_agreement **0.299** · rights_plan 0.184 · reverse_split 0.021 ·
  credit_facility 0.093 · **other 0.404**
- junk share (rights+reverse+credit) **0.297**
- Any-mention merger (primary OR secondary): 18/50 (0.36 unweighted)
- Merger bucket (n=15): de-SPAC 1/15; side: target 11, unclear 3, acquirer 1
- `other` (n=20) sub-labels: preferred_issuance 10; warrant-related 5; ch11_emergence 1;
  spinoff_charter 1; libor_transition 1; preferred_stock_elimination 1; other 1
- 5.03 co-occurrence: 31/50 (observation only — never a filter, per the registered rule)
- Cap buckets (split by source; lookups ran AFTER classification): current_survivor —
  micro 17, small 5, mid 4, large 3; **unknown 21 (0.42 — the delisting proxy; 19/50 have
  no live-map ticker at all)**
- SIC 2-digit concentration: 73 (services/tech) 11, 28 (pharma/chem) 8, 36 (electronics) 4,
  61/62 (finance) 5, others long tail; 1 SIC-unknown

## The frozen interpretive frame, applied
The frame (plan §3.0, committed before any tally existed) defined merger-dominated ≡
volume-weighted merger share ≥ 0.50 and junk-dominated ≡ rights+reverse+credit ≥ 0.50.
**Result: NEITHER fires — the cohort is "mixed"** (merger 0.299, junk 0.297, other 0.404).
The frame's pre-committed verdict readings were written only for the two dominance cases,
so they do not fire; that gap is itself recorded rather than filled post-hoc. What can be
said within the frame's rules:
- The merger bucket is NOT contaminated (de-SPAC 1/15 and acquirer 1/15 — both far below
  the pre-committed one-half thresholds), so a future *merger-conditioned* analysis is not
  pre-poisoned — but it would require a NEW pre-registration (the audit cannot re-scope).
- At ~30% merger share, any production verdict on `edgar:8k` measures a cohort whose
  majority is non-merger financing/defensive events (heavily micro-cap, 42% delisted-proxy)
  — a KILL would NOT cleanly indict the Lerman-Livnat merger pocket, and a positive verdict
  would not cleanly confirm it either. (Labeled: post-tally observation, consistent with —
  not part of — the frozen frame.)
- The cap_unknown share (0.42) is the one cap statistic allowed into conclusions: it
  predicts heavy survivorship pressure on the raw cohort's measurable fraction, consistent
  with the pre-registered expectation that raw = INSUFFICIENT is the base case.

## Side findings logged during the protocol
- `scout/calendar.py` holiday gap 2022–2024 (would have leaked filing-day closes into ~2–4%
  of production entries): **fixed and merged pre-production (#124)**, dates two-source
  verified.
- EFTS 500s are bursty; retry budget raised 2→5 (#122) after the veto cold-start failure;
  the enumeration needed an outer resume loop (bursts can still exhaust one chunk — the
  production runner's chunked resume handles this by design).
- EFTS `sics` can be empty (SIC-unknown bucket reported).

## Verdict of the protocol
All three registered gates are complete and recorded. **The production backfill runs are
UNBLOCKED** (the audit frames, it does not gate). Composition is committed here before any
forward-return measurement exists for the cohort.

## The 50-row table
| filed | accession | ticker | primary (type/side) | sub_label | secondary | 5.03 | SIC | cap |
|---|---|---|---|---|---|---|---|---|
| 2022-01-06 | 0001214659-22-000423 | CIK:1141807 | merger_agreement (conv/target)  |  | credit_facility |  | 6035 | unknown |
| 2022-03-14 | 0001193125-22-073994 | WT | rights_plan |  |  | y | 6211 | mid |
| 2022-04-01 | 0001552781-22-000302 | SWKHL | rights_plan |  |  |  | 6159 | unknown |
| 2022-07-07 | 0001193125-22-189511 | CIK:1255474 | merger_agreement (conv/target)  |  | credit_facility | y | 1311 | unknown |
| 2022-07-12 | 0001193125-22-192011 | CIK:1624658 | other | preferred_issuance | reverse_split | y | 2836 | unknown |
| 2022-08-22 | 0001104659-22-093107 | VAL | credit_facility |  |  |  | 1381 | mid |
| 2022-08-31 | 0001104659-22-096552 | DBGI | other | preferred_issuance | reverse_split+merger_agreement | y | 5600 | micro |
| 2022-10-03 | 0001193125-22-256805 | CIK:0877890 | merger_agreement (conv/target)  |  | credit_facility | y | 7372 | unknown |
| 2022-10-12 | 0001287032-22-000334 | PSEC | other | preferred_issuance |  | y | — | small |
| 2022-11-02 | 0001493152-22-030139 | CIK:1499717 | credit_facility |  |  |  | 7363 | unknown |
| 2022-11-18 | 0001213900-22-073874 | ILLR | merger_agreement (de-SPAC/unclear)  |  |  | y | 6282 | micro |
| 2022-11-21 | 0001104659-22-120846 | DVLT | other | warrant_registration_rights_amendment |  |  | 3674 | small |
| 2022-12-06 | 0001193125-22-299495 | SABR | credit_facility |  |  | y | 7370 | small |
| 2023-03-27 | 0001493152-23-009008 | VISL | other | preferred_stock_elimination |  | y | 3669 | micro |
| 2023-03-29 | 0001553350-23-000210 | DUOT | other | preferred_issuance |  | y | 7372 | micro |
| 2023-05-04 | 0001193125-23-135947 | ENB | rights_plan |  |  |  | 4610 | large |
| 2023-05-30 | 0001193125-23-156582 | TTMI | credit_facility |  |  |  | 3672 | large |
| 2023-07-06 | 0001140361-23-033342 | CIK:1493611 | other | libor_transition |  |  | 6189 | unknown |
| 2023-09-07 | 0001095315-23-000044 | CIK:1095315 | rights_plan |  |  |  | 7389 | unknown |
| 2023-09-08 | 0001213900-23-075471 | CIK:1491487 | merger_agreement (conv/unclear)  |  |  |  | 5990 | unknown |
| 2023-09-14 | 0001095315-23-000045 | CIK:1095315 | merger_agreement (conv/target)  |  | rights_plan |  | 7389 | unknown |
| 2023-10-23 | 0001104659-23-110660 | CIK:1760717 | merger_agreement (conv/target)  |  |  | y | 7374 | unknown |
| 2023-11-03 | 0000950170-23-058840 | APVO | rights_plan |  |  |  | 2834 | micro |
| 2023-11-06 | 0001140361-23-051496 | CIK:1651561 | merger_agreement (conv/target)  |  | credit_facility | y | 7389 | unknown |
| 2023-11-15 | 0001193125-23-277530 | CIK:1971543 | other | spinoff_charter |  | y | 2834 | unknown |
| 2023-12-19 | 0001477932-23-009242 | CIK:1443611 | reverse_split |  |  | y | 5960 | unknown |
| 2024-01-25 | 0001193125-24-015576 | AWHL | other | warrant_amendment |  |  | 2835 | micro |
| 2024-02-05 | 0001683168-24-000677 | WYTC | other | warrant_expiration_extension | credit_facility |  | 4822 | micro |
| 2024-02-27 | 0001193125-24-047061 | CIK:1124804 | rights_plan |  | merger_agreement | y | 7373 | unknown |
| 2024-02-28 | 0001437749-24-005900 | CRVO | other | warrant_amendment_ownership_cap_removal |  |  | 2834 | micro |
| 2024-03-25 | 0001749723-24-000022 | NFE | merger_agreement (conv/acquirer)  |  | other | y | 4924 | micro |
| 2024-04-23 | 0001104659-24-050573 | DVLT | other | warrant_amendment |  |  | 3674 | small |
| 2024-05-03 | 0001193125-24-130779 | GRTX | rights_plan |  |  | y | 2834 | micro |
| 2024-05-28 | 0001065059-24-000033 | LEU | rights_plan |  |  |  | 1400 | mid |
| 2024-07-08 | 0001104659-24-078355 | SW | merger_agreement (conv/unclear)  |  | credit_facility | y | 2650 | large |
| 2024-08-30 | 0001193125-24-211220 | CLSK | other | preferred_issuance |  | y | 6199 | mid |
| 2024-10-11 | 0001495320-24-000073 | VRA | rights_plan |  |  | y | 3100 | micro |
| 2024-12-03 | 0001193125-24-269937 | CIK:1293282 | merger_agreement (conv/target)  |  | credit_facility | y | 4822 | unknown |
| 2025-01-15 | 0001104659-25-003782 | CIK:1037676 | merger_agreement (conv/target)  |  |  | y | 1221 | unknown |
| 2025-01-15 | 0000950170-25-005647 | VRM | other | ch11_emergence | reverse_split | y | 5500 | unknown |
| 2025-02-13 | 0001171843-25-000796 | EDSA | other | preferred_issuance |  | y | 2834 | micro |
| 2025-04-14 | 0001140361-25-013659 | MNTS | other | preferred_issuance |  | y | 3760 | micro |
| 2025-04-16 | 0001193125-25-082841 | CIK:1828723 | merger_agreement (conv/target)  |  | credit_facility | y | 4911 | unknown |
| 2025-07-09 | 0001663577-25-000222 | IQST | other | preferred_issuance |  | y | 4813 | micro |
| 2025-09-10 | 0001641172-25-027052 | QCLS | other | preferred_issuance | merger_agreement | y | 2835 | micro |
| 2025-09-24 | 0001140361-25-035929 | CIK:1845022 | merger_agreement (conv/target)  |  | credit_facility | y | 7372 | unknown |
| 2025-10-09 | 0001654954-25-011611 | AISP | other | warrant_exercise_inducement |  |  | 7372 | micro |
| 2025-10-20 | 0001193125-25-243136 | CIK:1876588 | merger_agreement (conv/target)  |  | credit_facility | y | 3843 | unknown |
| 2025-12-04 | 0001849820-25-000296 | KITT | other | preferred_issuance |  | y | 3569 | micro |
| 2025-12-29 | 0001402829-25-000065 | ORN | credit_facility |  |  |  | 1600 | small |
