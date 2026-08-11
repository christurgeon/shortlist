# The autonomous scout is retired (2026-08-11)

**Decision:** `shortlist` is now two things — **research** (`/screen`, `/deep`, the 10-K
brief layer) and **portfolio** (positions: `/add`, `/hold`, `/remove`, `/thesis`). The
nightly discovery orchestrator is deleted. Its SEC/EDGAR **clients** survive as an
importable library with no caller.

> **Correction (2026-08-11, from code review).** An earlier draft of this line said
> "positions, **the monitor**". Only half the monitor survived. `bot/monitor.py`'s
> `compute_alerts`/`heartbeat` are pure and still tested, and `report/sections.py` still
> renders a `positions_monitor` payload — but the only producer of that payload was
> `scout/daily.py`, so **held-name 8-K alerting no longer fires**. `portfolio.monitor.enabled`
> is now `false` rather than advertising a feature that cannot run. Re-wiring the alerts into
> `/portfolio` is an open decision, tracked in TODO.md.

This is a scope decision, not a bug fix. It is written down because the code that
implemented the scout is gone and the reasoning would otherwise go with it.

---

## 1. Why

### 1.1 Every originator that reached the evaluator failed it

| signal | verdict | evidence |
|---|---|---|
| `edgar:13d_stake_increase` | **INSUFFICIENT** — monthly alpha −2.0% raw / −4.4% scored, both CIs entirely below zero, n=1,422 events | `2026-07-19-13d-a-stake-increase-backfill-verdict.md`, `scout/validate-latest.json` |
| `edgar:8k` | **INSUFFICIENT** | `2026-07-08-eightk-composition-audit.md` |
| `edgar:buyback_auth` | **KILLED** | `2026-07-11-buyback-backfill-kill.md` |

The two originators shipping ON at the highest weights — `edgar:activist_13d` (1.5) and
`edgar:13f` (1.0) — were **never evaluated at all**. They shipped on published literature
priors, not on anything this system measured.

### 1.2 The measurement apparatus was blocked on spend that is not happening

`2026-08-06-discovery-breadth-plan.md` Track D: the form4 cohort, the regime-break audit,
the buyback attribution and the 8-K coverage split are all *blocked, needs price history*.
The scout's entire justification was "the selection ledger earns signals their weight over
time" — but the ledger was accruing picks that could not be scored without a paid price
feed that is not being purchased.

### 1.3 The funnel could not produce what it was wanted for

Free EDGAR event flow is nano-cap dominated: activism and insider buying structurally
happen at small caps. `2026-08-07-funnel-gate-mismatch.md` measured a discovery layer whose
`edgar:activist_13d` median pick was **$50M** against a `gates.min_market_cap` then set to
**$2B**, and showed that the obvious fix — filtering discovery to the gate — **empties 13
of 25 sessions**. (The gate was subsequently lowered to $300M on 2026-08-07 in response to
that audit; the composition problem it documented is what mattered here, not the threshold.)

Raw discovery ran 3–13 candidates/night against a `daily_x` of 15, so the deep-screen budget
bound **once in five sessions, by two names**. The funnel was starved at the top, not
clipped at the bottom — loosening anything downstream would have bought nothing.

### 1.4 The cost was disproportionate

Measured immediately before deletion:

| | scout | repo | share |
|---|---|---|---|
| source LOC | 12,373 | 26,498 | **47%** |
| test LOC | 20,455 (65 files) | 34,500 | **59%** |
| config (JSON bytes) | 3,510 | ~9,100 | **39%** |

Half the codebase and 60% of the test suite, to produce ~9 candidates a night of which most
were pre-rejected.

### 1.5 What the scout *did* get right, and where it went

`edgar:13f` was the one originator without the composition problem — marquee funds hold
marquee names. Its complete pick history (6 of 203):

| date | ticker | composite | gated | market cap |
|---|---|---|---|---|
| 2026-07-13 | AON | 66.1 | no | $76B |
| 2026-07-13 | V | 60.8 | no | $672B |
| 2026-07-14 | **GOOGL** | 59.8 | no | $4,292B |
| 2026-07-14 | KKR | 39.0 | no | $87B |
| 2026-07-14 | SNDK | 50.3 | yes | $258B |
| 2026-07-14 | GLD | 40.0 | no | — |

Five of six ungated; AON and V are two of only 34 picks out of 203 that ever scored ≥60. It
fires ~4 days a quarter (13F deadlines: Feb 14 / May 15 / Aug 14 / Nov 14). **The 13F client
is kept** — see §3.

## 2. What was deleted

`daily.py` (1,551), `signals.py` (1,268), `validate.py` (1,058), `backfill.py` (943),
`preregister.py`, `state.py`, `funnel.py`, `budget.py`, `picks.py`, `firehose.py`,
`factors.py`, `quality_floor.py`, `investable.py`, `wsb_novelty.py`, `delisting.py`,
`short_interest.py`, `stake.py`, `buyback.py`; the `shortlist-scout` entry point; the
`scout:` config block; the `shortlist-scout` systemd service + timer; and ~63 test files.

**Preserved as evidence, not code**, under `2026-08-11-scout-retirement/`:

- `ledger.json` — all 203 picks across 29 sessions in full, plus per-day per-signal firehose
  counts. The raw state was `/opt/shortlist/state/scout_state.json` (373 KB).
- `preregistrations/` — the seven committed pre-registration YAMLs. `validate.py:load_prereg`
  read these via `git show HEAD:<path>` precisely so an uncommitted threshold edit could not
  read as pre-registered; that property is why they are worth keeping after the evaluator.

## 3. What survives, and the deal you are taking

`src/shortlist/edgar/` — `thirteenf`, `insider`, `dera`, `index`, `eightk`, `quality`,
`calendar`, `sec_throttle`, `cusip_map`, `cik_tickers`, `symbology`, `stake_pct`,
`_ticker_rules`, `models` (`Emission`).

**These have no production caller.** Nothing on the `/screen` or `/deep` path imports them.
That is deliberate — the data is still worth reaching by hand during research — but it has a
cost worth stating plainly:

> CI keeps pinning their **parse shapes**. It does **not** catch SEC or edgartools changing
> shape upstream, because the live fetch tests are `pytest.mark.live` + `skipif(not
> SEC_IDENTITY)` and skip by default. Run `SEC_IDENTITY=... uv run pytest -m live` before
> trusting a client after a long gap.

This is not hypothetical: `edgartools` `standard_concept` drift already broke extraction once
(`2026-07-31-edgar-concept-match.md`).

Dropped with the orchestrator because their signals had failed verdicts and nothing else used
them: `buyback` (KILLED), `stake` (INSUFFICIENT — only the pure `stake_pct` extraction was
kept, for `backtest/edgar_history.py`), `short_interest` (the harness `FinraSource` already
covers this), `wsb_novelty` and `delisting` (scout-only).

### 3.1 Three symbols had to be rescued first

The clients were coupled to modules on the drop list, so deleting in the obvious order would
have broken the keep-set:

| symbol | was in | needed by |
|---|---|---|
| `junk_suffix` | `eightk.py` | `thirteenf.py`, `insider.py` |
| `_FIFTH_LETTER_SUFFIXES` | `short_interest.py` (dropped) | `junk_suffix` |
| `normalize_items` | `delisting.py` (dropped) | `eightk.py` |

Now single-sourced in `edgar/_ticker_rules.py`. Two more were found the same way, in the
**kept** backtest package: `ols`/`_residuals` (`validate.py` → `backtest/_ols.py`, needed by
the shipped `momentum.residual` leg) and `stake_pct_from_filing` (`stake.py` →
`edgar/stake_pct.py`).

## 4. One scoring fix shipped alongside

`validity.min_composite_components: 1` (`scoring.py`). A composite must rest on at least one
real sub-score; `risk` is a composite-only **tilt** and does not count.

The defect, live on 2026-08-10: **BRVE ranked #1 at composite 100.0** with all six components
null and confidence 0.0. The weight redistributed onto the risk tilt alone, which read 100
because the issuer reports no debt. `scored` missed it because the bucket gate reads
`True if bucket == "unknown" else …`, and `unknown` is the majority bucket.

**It is a COUNT, not a weight threshold, and that distinction was forced by a committed
guard.** The first attempt used a confidence floor of 0.20 and broke
`test_scoring_abstention.py:test_unknown_momentum_only_name_still_scored` — a momentum-only
name sits at confidence ~0.08 and is pinned as scored, while BRVE sits at 0.0. No weight
threshold separates those safely. The categorical rule does, because BRVE had *zero*
components and the momentum-only name has one.

No-op when the config key is absent, so every pre-2026-08-11 cohort verdict stays
bit-identical. `tests/test_scoring_composite_floor.py` pins both cases.

## 5. Deliberately NOT done

- **The gated-name report demotion.** Planned, then dropped: it made sense for a discovery
  digest where the funnel chose the names, but `/screen AAPL MSFT` is a list the user asked
  for, and hiding a gated name they named would be wrong. `models.py:rank_key` already sorts
  `scored` first, so the §4 fix demotes the BRVE case on its own.
- **Trimming `report/`.** `sections.py` still carries scout-shaped sections (funnel counts,
  per-signal status, prior-picks scoreboard, `_ValidationScoreboard` — whose validator no
  longer exists). The bot fabricates a `RunManifest` with `signals=[]` to satisfy that API
  (`bot/telegram.py:_interactive_manifest`). It renders correctly; it is dead weight, not a
  defect. Tracked as follow-up.
- **Re-litigating any of §1.** These verdicts are measured and committed. Reversing one needs
  new evidence, not a new reading of the old.
