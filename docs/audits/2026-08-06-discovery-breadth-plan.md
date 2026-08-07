# Discovery-breadth plan — what to build while cohort measurement is blocked (2026-08-06)

**What this is:** the pickup plan for the discovery-layer workstream logged in
`docs/audits/2026-08-05-session-log.md`, written under the constraint that **the paid price
feed is not being purchased**, so every cohort-measurement item in that log's §6 stays
blocked.

Committed to the tracked `docs/audits/` tree, not `docs/superpowers/specs/` — that directory
is gitignored (`.gitignore:37`, 0 files tracked) and is where two enablement artifacts already
evaporated.

**Status (updated 2026-08-07):** reviewed adversarially, corrected, and partly implemented.
A1 and A5 are DONE; A2 is preliminary; A3 is deferred on evidence; A4 is split and answered.
Track C remains **gated**, now on B0 as well.

> **⚠ READ THIS FIRST — the largest finding of this workstream is not in Track A.**
> `docs/audits/2026-08-07-funnel-gate-mismatch.md`: `gates.min_market_cap` is **$2B**, while
> `edgar:activist_13d`'s median pick is **$50M**. Across the three enabled originators, **~80%
> of deep-screen slots are spent on names the committed gate is configured to reject.** The
> obvious fix — a market-cap pre-filter — was measured before being built and would empty
> **13 of 25 sessions**. Both the discovery layer and the gate are individually defensible;
> together they are mismatched. Resolving that is a live-gate decision requiring its own
> evidence, and it is deliberately **not** taken here.

---

## 1. The thing this plan corrects about its own first draft

The session opened by proposing a **standing universe sampler** — a non-event originator
drawing a fixed nightly quota from the listed universe so the digest is never empty. The
design survived review (§5 keeps it). **Its premise did not.**

`sunny-shimmying-parasol.md` Phase 2 already commits the standing screen to a
**spike-first gate**: *"Spike first (time-boxed, no production wiring)"*, decided on
*"what fraction of output lands in $0.3–10B"*, because *"a standing screen that reproduces
that barbell is worthless regardless of how many rows it emits."* The first draft went
straight to a build. That is the read-past-a-committed-guard failure `CLAUDE.md` records as
costing four retracted conclusions on 2026-07-26.

### 1.1 Measured empty-day incidence — the premise fails

All 28 committed production manifests under `/opt/shortlist/scout/*/manifest.json`:

| metric | count | share |
|---|---|---|
| sessions | 28 | — |
| `raw == 0` | **1** | 4% |
| `raw < 3` | 2 | 7% |

The single empty session is **2026-08-04**, which had **four** failed originators and is fully
attributed to the resolver + throttle defects fixed in `11c6006` / `3744612`. There is no
second instance. The "structural empty-day gap" is real as a *property* — every originator is
event-triggered — but its realized incidence is one occurrence with a different, fixed cause.

### 1.2 The regime that actually matters is n=3

`wsb_hype` was demoted in `7398ef2` (#151) and reached production for the **2026-07-30**
session. Only five sessions have run in the current origination regime, and two were degraded:

| session | raw | delivered | health (replayed through the real `run_health()`) |
|---|---|---|---|
| 2026-07-30 | 3 | 2 | degraded (`yahoo_screener`) |
| 2026-07-31 | 6 | 5 | degraded (`yahoo_screener`) |
| 2026-08-03 | 3 | 3 | degraded (`yahoo_screener`, `edgar_activist_13d`) |
| 2026-08-04 | 0 | 0 | degraded (`yahoo_screener`, `edgar_activist_13d`) |
| 2026-08-05 | 13 | 10 | **healthy** |
| 2026-08-06 | 8 | 8 | **healthy** |

> **CORRECTED 2026-08-07.** This table first read "three clean sessions, delivering 2/5/10"
> and scored 08-04 as "4 originators dead". Both were wrong.
> `yahoo_screener` was still **enabled** and WAF-failing through 08-04 (it was disabled on
> 08-05), so 07-30 and 07-31 replay as `degraded`, not clean. And the "4" counted every
> `ran=False` row, double-counting `finnhub_news`/`wikipedia` — enrichment signals whose
> `ran=False` is a *consequence* of `raw=0`, as §5.2 of this same document argues.

**Two genuinely healthy sessions, delivering 10 and 8.** The correction cuts both ways, and
the second half matters more: the sample is *smaller* than claimed, but when nothing was
broken the funnel delivered 8–10 names. That weakens the case for a standing sampler rather
than strengthening it. Two observations cannot size a remedy; the cheapest way to get more is
to let the scheduled runs accumulate while doing Track A.

### 1.3 Two further over-claims, recorded so they are not re-made

- **The control-arm argument does not justify the live sampler.** The strongest case for a
  random standing draw is that it would give every cohort the matched null baseline the
  evidence base currently lacks. True — but that control can be built **retroactively** from
  point-in-time `symbology.Symbology` draws, with immediate statistical power instead of
  accruing ~500 names/year, and with no production surface at all. A benefit of the
  retroactive cohort was used to argue for the live feature.
- **Digest padding has a real cost, and it is the audit's own remedy #6 in reverse.**
  `2026-08-05-discovery-funnel-audit.md` §9 disabled the Yahoo screener because a
  permanently-failing signal *"trains you to ignore ✗ marks."* Filling slots with names that
  are meaningless by construction trains the reader to discount the digest the same way. The
  `quiet` verdict shipped on 2026-08-05 exists precisely so an empty night reads as
  informative rather than broken.

---

## 2. Correction to committed documentation — `edgar_form4` weight

`CLAUDE.md`, `sunny-shimmying-parasol.md` and `2026-08-05-session-log.md` all state that
`edgar_form4` *"ships ENABLED at weight 1.5 — the joint-highest of any originator, the same
tier as 13D."*

**It is weight 1.0** (`config.yaml:736`). PR #152 (`d31170e`) lowered it from 1.5 to 1.0 in
the same commit that shipped the signal; the prose describing the pre-review value was never
updated.

This matters because it is load-bearing for the framing *"the two highest-weight enabled
originators are the least defensible things in the repo."* `edgar_activist_13d` alone sits at
1.5. `edgar_form4` is tied with `edgar_13f` at 1.0. **The substantive point survives** —
`edgar_form4` is enabled, moves live selection, and has never been measured — but it is one
tier lower than three documents claim. Fixing the prose is Track A item A1.

---

## 3. Track A — do now (unconditional value, no gate)

Every item here is worth having regardless of what any later cohort verdict says. None
requires price history.

| # | Item | Why now | Done when |
|---|---|---|---|
| **A1** | Correct the `edgar_form4` weight-1.5 claim in `CLAUDE.md`, `sunny-shimmying-parasol.md`, `2026-08-05-session-log.md` | Three committed documents assert a wrong number that anchors a priority argument (§2) | All three read 1.0; the "joint-highest" framing is amended, not deleted |
| **A2** | Read `sec_requests` from the 2026-08-06 manifest onward; record the figures | First production measurement of the shared SEC budget. Confirms or refutes the 2026-08-04 cascade, which is still **inferred from timing correlation** | A short evidence note under `docs/audits/` with ≥3 sessions of per-consumer counts |
| **A3** | Enable the quality floor (`scout.quality_floor.enabled: true`) | Evidence already committed in `2026-08-05-quality-floor-evidence.md` (7 of 135 past picks were slot-wasting, 6 of 7 from `edgar:activist_13d`). **Must land after A2's first clean baseline** — it adds ~16 `secframes` requests that would contaminate it | Deployed; a subsequent manifest shows floor drops named in `notes` |
| **A4** | Size `edgar_index_daily_cap` from A2. **`daily_x` is NOT sizeable from A2** | §4e of the session log: `daily_x: 10` is **not** an FMP-quota constraint (the nightly digest runs the free chain), so it is an unexamined config choice. Do not raise it blind | A recommendation backed by measured SEC headroom, or an explicit "leave as-is" with the number that says so |

**Ordering is load-bearing: A2 before A3.** The session log is explicit that enabling the
quality floor before the first `sec_requests` baseline pollutes that baseline.

### A-track status after the 2026-08-07 review and implementation

| item | status |
|---|---|
| **A1** | **DONE** — `CLAUDE.md` corrected to weight 1.0; the session log carries a superseded note; this document's three factual errors are fixed in place (see the §1.2 correction box, `daily.py:663`, and B0) |
| **A2** | **PRELIMINARY, bar NOT met.** One session (2026-08-06) of the required ≥3. Recorded in `docs/audits/2026-08-07-funnel-gate-mismatch.md` §2. Also **fixed**: 1.3% of the budget was unattributable — four unlabelled `throttle()` sites now carry consumer labels, bound by an AST test |
| **A3** | **DEFERRED, deliberately.** Proposed and pulled — see §5 of the gate-mismatch audit. Blocked by A2's own bar, and the 5.2% evidence is same-ledger with no held-out set |
| **A4** | **SPLIT.** `edgar_index_daily_cap`: **leave at 2500** — never binds (peak 928 = 37%), and lowering it truncates a structured prefix. `daily_x`: **not answerable from `sec_requests` at all** — deep-screen EDGAR fetches use the harness `EdgarSource`'s own `_EDGAR_MAX_CONCURRENCY` semaphore, outside the shared throttle and therefore uncounted. Needs separate instrumentation |

**A4's split is a correction to this document**, which bundled two unrelated budgets under
one evidence source. Only one of them is measured by the artifact A2 produces.

### A5 (new, DONE) — mutual funds were reaching the digest as stock picks

Not in the original Track A; found while measuring for it. `BBASX`, an open-end mutual fund,
was delivered **ungated at composite 100.0** as the top-ranked name of 2026-07-27. Fixed by
adding `X` to `_FIFTH_LETTER_SUFFIXES` and applying `_junk_suffix` to `edgar_form4` — the one
EDGAR originator that never did. Proven neutral for every committed cohort verdict. This also
**corrects `2026-08-05-session-log.md` §4b**, which declined to build a security-type filter
on the belief that the affected originators were retired; one was merely *renamed*, and the
ETF case came from the live 13F signal. Evidence: gate-mismatch audit §3.

**A2 note (2026-08-06):** the `sec_requests` block visible in the *local*
`scout/2026-08-05/manifest.json` is the previous session's **offline simulation**
(`edgar_form4: 930`, 97.9% of the run's draw). The production manifest at
`/opt/shortlist/scout/2026-08-05/manifest.json` has `sec_requests: null`, because that run
predates the deploy. The first genuine figure comes from the 2026-08-06 22:30 UTC run.

---

## 4. Track B — the spike that gates the standing screen

Time-boxed, **offline, no production wiring**. This is Phase 2 of
`sunny-shimmying-parasol.md`, executed as written rather than skipped.

### B1. Composition of the banded universe

`api.nasdaq.com/api/screener/stocks` was re-probed from this box on 2026-08-06: **HTTP 200,
`application/json`, 4,177 NASDAQ records in one request**, carrying `symbol`, `name`,
`lastsale`, `marketCap`. Three requests (NASDAQ / NYSE / AMEX) cover the listed universe. It
is **not** on the sec.gov host, so it draws zero SEC budget.

Measure, and write down:

- The full listed-universe market-cap distribution, and specifically **what fraction lands in
  $0.3–10B** — the band the 2026-07-26 composition audit found the funnel barbelled around.
- What survives the existing junk filters (5th-letter security suffixes, `deny_list`) and the
  quality floor's two rules.
- Whether the surviving population is dominated by shells and non-operating entities, which
  is the outcome that would make a standing screen worthless.

### B2. The thin-day counterfactual

Replay the last ~10 sessions' manifests and answer: **on how many nights would a reserved
sampler quota actually have changed the digest?** On a 13-raw night it displaces two
event-originated names; on a 3-raw night it adds two. Both numbers are needed, because the
cost and the benefit are the same mechanism.

Do this against **committed manifests**, not a live re-run.

### B0. Reconcile against the prior committed decision — do this FIRST

`docs/audits/2026-08-05-standing-screen-data-source.md` §6 concludes: *"Do NOT build a
standing full-universe originator… Build it as a FILTER on existing originators' output
instead"* — the decision that produced the quality floor. **This plan resurrected a standing
sampler without citing it.** That is the same read-past-a-committed-guard pattern §1 opens by
correcting, committed one day earlier.

A neutral-sign random *control draw* is plausibly a different animal from the ranked
full-universe *originator* that §6 rejected — but that argument has to be made explicitly and
recorded, not assumed by omission. **No Track B work starts until it is.** If it cannot be
made, Track C is dead and that is the finding.

### B3. Gate

Build Track C only if **all three** hold (B0, plus):

1. B1 shows a substantial, non-shell population in $0.3–10B — i.e. the sampler would not
   reproduce the nano-cap barbell it exists to correct.
2. Accumulated sessions show thin or empty nights recurring **after** the `11c6006` /
   `3744612` fixes, at a rate that a reserved quota would meaningfully change.

If either fails, **record the non-build as the finding** and stop. Disabling or declining a
signal that cannot earn its slot is a win, not a regression.

**Deliverable:** `docs/audits/2026-08-XX-standing-screen-spike.md`.

> ⚠ **Do not hand-probe the Yahoo screener from this box.** It trips the WAF IP-wide and the
> `v8/finance/chart` endpoint the whole scorer depends on starts 429ing. The Nasdaq endpoint
> is a different host and is safe to probe; Yahoo is not.

---

## 5. Track C — the standing universe sampler (designed, NOT approved to build)

Kept here in full so the Track B gate has something concrete to accept or reject. **Nothing in
this section ships without B3 passing.**

**Framing:** a fixed nightly quota (2–3) drawn from the listed universe, banded to $0.3–10B,
passed through the quality floor, chosen by a deterministic rotating cursor. It makes **no
directional claim**. It is tagged as its own originator and labelled in the digest as a
baseline sample, not a pick.

### 5.1 Units

| Unit | Kind | Responsibility |
|---|---|---|
| `data/nasdaq_universe.py` | new shared leaf (`_form4.py` / `finra.py` pattern) | pure `parse_universe(payload)` + day-cached fetcher, 3 requests, `(symbol, market_cap, last_sale)` and nothing else — no ranking, no scoring |
| `scout/sampler.py` | pure | cap-band filter, junk-suffix drop, deterministic rotating cursor over the sorted banded universe; cursor is one integer in `ScoutState` |
| `StandingUniverseSignal` (`scout/signals.py`) | signal | wires the two behind the existing `register()` seam; `is_discovery=True`, flat `strength`, `signal="standing:universe_sample"` |
| `budget.select` | extended | `select(candidates, daily_x, reserve_signal=None, reserve_n=0)`; **byte-identical** to today when the reserve is absent or zero, pinned by a test |
| `scout/report/sections.py` | new section | labels sampled names as baseline, not pick |

### 5.2 Two hazards that are load-bearing

**The sampler would silently destroy the `quiet` / `degraded` distinction.** `daily.py:663`
sets `raw = len(emissions)`, and `models.run_health` returns `quiet` only when `raw == 0`. A
sampler that always fires makes `raw >= K` on every session, so a night where *every* event
originator died would classify as `healthy`. That is the audit's remedy #6 re-broken through
the back door. **`run_health` must receive the event-originator count**, not the total, and
`RunManifest` must carry both numbers.

**`prefilter` runs after emission and can eat the whole quota.** It drops cooldown and held
names (`funnel.py:24`), so a sampler drawing exactly K could deliver an empty digest anyway —
the precise bug the feature exists to prevent. The sampler therefore **over-draws** (default
3×K) and the reserve takes the top survivors in `select`.

### 5.3 Prerequisites and costs, stated plainly

- The quality floor must be **on** (Track A3) — it stops being an optional slot optimisation
  and becomes the sampler's junk filter.
- The reserve comes **out of** `daily_x: 10`, not on top of it, until A4 says otherwise. On a
  busy night two event-originated names are displaced.
- The digest will look duller. Most sampled names will be unremarkable, because that is what a
  control arm is.
- `api.nasdaq.com` is undocumented — the same fragility class as the Yahoo screener just
  retired. The mitigation is that its failure is **loud** (`run_health` → degraded), not that
  it will not happen.

### 5.4 Pre-registration with an inverted expectation

A standing screen changes which names surface, so it needs a committed prereg **before** it
can influence the digest (`preregister.py:load_prereg` parses `git show HEAD:<path>`, so an
uncommitted YAML is correctly reported unverifiable).

Its **expected sign is neutral** — this is a control arm, and "no edge" is the passing result.
A strongly positive cohort would be evidence the **cap band** is mispriced, not that the
sampler is clever. That reading must be committed in advance so it cannot be reinterpreted
afterwards.

---

## 6. Track D — blocked or deferred, with the reason

| Item | Status | Reason |
|---|---|---|
| Phase 0.3 — wire the `form4` backfill cohort | **blocked** | Needs price history for the cohort verdict. The stateful `assemble_factory` + look-ahead test could be *built* now, but would sit unmeasured |
| Phase 0.2 — regime-break audit (6 preregs) | **blocked** | Split-sample re-measurement needs prices |
| Attribute the buyback verdict (−0.84%/mo committed vs −0.14%/mo on replay) | **blocked** | Needs a like-for-like replay pinned to the original as-of |
| `8k-neg` coverage/attrition split | **blocked** | ~2,884 price fetches; also a Yahoo-load event |
| Phase 1.2 — Form 25 / 25-NSE delisting source | **deferred** | Buildable now and pure correctness (kills the archive.org single point of failure in `symbology.py`), but *validating* that it shifts no committed cohort verdict needs the replays, which need prices. Build only if Track A and B complete early |
| Phase 3 originators (RegSHO, `UPLOAD`/`CORRESP`, `NT 10-K`, 8-K 4.01/1.02) | **deferred** | Each ships disabled at weight 0.5 and stays disabled until a cohort measures it. Near-term funnel impact is ~zero, so they are the wrong thing to spend this window on |
| A retroactive control cohort from point-in-time `symbology` draws | **candidate** | The genuinely unblocked half of §1.3. Partially price-limited, but the 2,512-file `.cache/famafrench` store covers some of it. Size it after Track B |

---

## 7. Verification

Before any merge, in this order — a lint failure is a hard gate:

```bash
uv run ruff check src tests
uv run pytest -q                 # 2400 passed, 6 skipped at 7dd1190 (verified 2026-08-06)
```

Live runs use a **scratch `state_path` and `artifact_dir`** so production state and the
scheduled 22:30 session are untouched.

**Deploy** — `/opt/shortlist` is a git checkout of `main`:

```bash
systemctl is-active shortlist-scout.service          # MUST be inactive; never deploy 22:30–22:35 UTC
cd /opt/shortlist && sudo git pull --ff-only
sudo systemctl restart shortlist-bot.service         # git pull alone NEVER updates the bot
git -C /opt/shortlist log --oneline -1               # verify; never trust the installer exit code
```
