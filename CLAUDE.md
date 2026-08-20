# CLAUDE.md

Orientation for Claude Code in this repo: **what exists, and where the authority for it
lives.** Deliberately short. Detail belongs next to the code it constrains — a module
docstring or `docs/` page can be reviewed in the same diff as the change it governs, which
is why the gotchas that used to be inlined here are pointers now.

**Before changing a feature, read its authority file.** If this file and an authority file
disagree, the authority file wins and this one is stale — fix it.

## What this is

A quantitative stock pre-screen: pull fundamentals, score quality / moat / growth /
value / momentum / insider / risk, rank a shortlist for a human deep dive. Config-driven
via `config.yaml` (thresholds, weights, gates) — tuning should never need a code change.

## Design premise — read before adding a signal

This is a **triage funnel for a human deep dive, NOT a return-predicting alpha model.**

- We validate on ~80–238 free-tier, survivorship-biased, currently-listed names, not
  CRSP/Compustat. At that scale most factor legs are indistinguishable from noise — a
  single-universe `t≈2` is usually noise (buyback, leverage tilt and accruals all failed to
  replicate). **Stop adding scoring legs hoping one crosses `t=2`.** New legs are gated hard
  on reproducible cross-universe rank IC.
- The real edge is in **event-driven discovery** (13D/13F/insider/buyback/8-K originators),
  not the composite. Improve what *feeds* the funnel.
- **Measure first, kill on evidence, commit the evidence.** Every enabled signal that moves
  live scores needs a reproducible verdict under the tracked `docs/audits/` tree — **not**
  the gitignored `docs/superpowers/specs/` (two enablement artifacts already evaporated
  from there). Disabling a leg that can't earn its slot is a win, not a regression.
- **A committed guard outranks your reading of the numbers.** When a pre-registered floor, a
  test or a documented rule disagrees with a story built from the data, the guard wins until
  you can state precisely why it's wrong (2026-07-26 postmortem: four conclusions were
  retracted because a floor everyone assumed was wrong turned out to be correct).

## Dev workflow (uv)

```bash
uv sync                      # core + dev deps; uv.lock pins everything
uv sync --extra edgar        # add the SEC EDGAR source (also: --extra bot, --extra fred)
uv run ruff check src tests  # lint gate — exactly what CI runs
uv run pytest                # full suite
uv run pytest tests/test_scoring.py::test_norm_endpoints_midpoint_and_clamp
uv run shortlist --demo      # offline, no keys
SEC_IDENTITY=you@example.com uv run pytest -m live   # real API calls, skipped by default
```

`pip install -e .` still works as a fallback.

## Architecture in one pass

```
data/sources/*  →  merge_snapshots  →  TickerSnapshot  →  bridge  →  StockMetrics
                                                                        ↓
                                     ScoreCard  ←  scoring.score()  ←───┘
```

The async `httpx` **harness** (`shortlist.data.*`) is the sole production data layer.
`Source`s live in the **`data/sources/` package**; the `_REGISTRY` in its `__init__.py` is
the authoritative list and is what `--provider` / `config.yaml: harness_sources` resolve
against — **not** `providers/__init__.py:build_providers`. `merge_snapshots`
(`data/models.py`) combines them into an audited `TickerSnapshot`;
`bridge.py:snapshot_to_metrics` flattens it to `StockMetrics` (unavailable fields stay
`None`); `scoring.py:score` produces the `ScoreCard`. `coverage.py` annotates every card
with per-source fetch status, so a low sub-score is always distinguishable from a missing
one.

**`--provider` overrides `harness_sources` entirely** — omit it on the default path or
keyless sources get silently dropped.

Everything else orchestrates that pipeline; none of it adds scoring logic:

| Surface | Entry point | Authority |
|---|---|---|
| Screener CLI | `shortlist` | `README.md` |
| Raw snapshots | `shortlist-harness` | `HARNESS.md` |
| Backtest (rank IC, quantile spreads, `--fit`) | `shortlist-backtest` | `HARNESS.md` → Backtesting, `docs/ASSESSMENT_GAPS.md` |
| Point-in-time capture (scheduling ships OFF) | `shortlist-accumulate` | `HARNESS.md` → Feeding the snapshot path |
| Telegram bot — `/screen`, `/deep`, position monitor | `shortlist-bot` | `docs/TELEGRAM.md` (`docs/POSITION_MONITOR.md` is design-time) |

Run **one** bot instance — two concurrent `getUpdates` pollers 409.

## Where the authority lives

| Before you touch… | Read |
|---|---|
| Scoring, gates, flags, sector abstention, optional legs | `docs/SCORING.md` |
| A `config.yaml` knob | the comment above it — every non-obvious default carries its rationale inline |
| Source merge semantics, year-joined backfill | `data/models.py`, then `docs/STATEMENTS_MERGE.md` (design) |
| The bridge / derived metrics (ROIC, value legs) | `data/bridge.py` docstrings + inline comments |
| EDGAR statement extraction | `providers/_edgar_facts.py` docstring |
| The `shortlist/edgar/` client library | `edgar/__init__.py`, `docs/EDGAR_CLIENTS.md` |
| The `/deep` research layer | `docs/RESEARCH.md`, then the `research/*.py` docstrings |
| Deploying | `deploy/README.md` |
| Data-source roadmap / what's shipped | `docs/DATA_SOURCES.md` |
| Signal research and priors | `docs/PREDICTIVE_SIGNALS_RESEARCH.md` |
| Known gaps, unvalidated assumptions | `docs/ASSESSMENT_GAPS.md`, `TODO.md` |
| Why a past decision went the way it did | `docs/audits/YYYY-MM-DD-*.md` |

**Three doc classes, and only one describes today.** `docs/SCORING.md`, `docs/RESEARCH.md`,
`docs/TELEGRAM.md`, `docs/EDGAR_CLIENTS.md`, `HARNESS.md` and `README.md` track current
behaviour. `docs/audits/` is **dated evidence** — cite it for *why*, never as a description of
how the code behaves. `docs/PLAN_*.md`, `docs/POSITION_MONITOR.md` and `docs/STATEMENTS_MERGE.md`
are **design-time**: they record intent at the time of writing and their file/line references
are not maintained. Read the code for behaviour; read these for the reasoning behind it.

## Rules that span files

These are the constraints no single module can enforce — the reason to keep them here.

- **Shared extraction leaves are edited once.** `providers/_form4.py` (Form 4 aggregation)
  and `providers/_gaap_tags.py` (GAAP tag sets) have multiple importers. But
  `providers/_edgar_facts.py` and `providers/_xbrl_facts.py` are **separate** extractors —
  the harness uses the former, the backtest the latter. Changing one does not change the
  other; decide deliberately which needs the fix.
- **One process-wide sec.gov throttle** (`edgar/sec_throttle.py`). Never give a client its
  own — that broke the funnel outright on 2026-08-04.
- **Redact before printing.** Any error string that may embed a request URL MUST pass
  through `env.py:redact_secrets()` before it is printed, logged or stored.
- **Gates vs flags.** Gates are hard filters and set `passed`; flags are advisory and must
  never touch `passed` / `composite` / `scored`. New gate and flag names must be declared in
  `scoring.py` (`KNOWN_GATES` / `KNOWN_FLAGS`) and documented in `bot/glossary.py` — CI
  AST-scans the emitters and fails otherwise.
- **A missing sub-score is excluded and its weight redistributed**, never zeroed. Zeroing
  penalizes a name for a data gap.
- **`/deep` grounding is per-segment.** Anything added to the prompt that is *not* filing
  text must stay out of the quote-verification haystack, or a computed value can pass itself
  off as a filing fact. `docs/RESEARCH.md` → Quote verification.
- **`config.yaml` has one top-level key per name.** Two `value:` blocks are valid YAML that
  silently keeps only the last. Enabling two `value` knobs means one key with two children.
- **The deploy smoke test stays read-only.** It runs `shortlist --demo` against offline
  fixtures; anything writing to `state/` pollutes live data on every deploy.

## Deploying to the VPS

The live bot runs from **`/opt/shortlist`**, a git checkout of `origin/main`. Editing this
repo changes nothing in production until deployed.

```bash
cd /opt/shortlist && sudo git pull && sudo bash deploy/install_opt_shortlist.sh
```

Idempotent; handles sync, `uv sync`, units, `daemon-reload` and the bot restart. Always
verify afterwards — `git -C /opt/shortlist log --oneline -1` plus a grep for a symbol you
just added.

Failure modes, in-place-run semantics, inline unit generation and rollback:
**`deploy/README.md`**. `tests/test_deploy_units.py` pins the installer's behaviour.

## Secrets

Keys load from the environment or the root `.env` (gitignored; see `.env.example`,
`env.py:load_env()`). Run from inside the repo so `.env` is found.

## Scale

Free tiers fit a watchlist, not a universe: ~13 FMP calls/ticker against 250/day.
`shortlist-accumulate` caps at 15 tickers/day, the bot caps `/screen` at 10. A daily S&P 500
run needs FMP Starter or a warm cache (`cache.py`). FMP free-plan quirks — `/stable/` only,
per-symbol gating, paid insider endpoint — are documented in `data/sources/fmp.py`.

## Skills

Tracked in git, Claude Code workflows, **not** part of the Python package.

- **`/run`** — end-to-end screener: gather tickers → `uv run shortlist --json` → interpret
  scores, gates and coverage. `.claude/skills/run/SKILL.md`.
- **`/prospect`** — web-only weekly discovery; returns a 5–8 name brief plus a copy-paste
  `/deep` block. `.claude/skills/prospect/SKILL.md`. **This is the only discovery surface**
  — the package has no universe scan (`--tickers` or `--demo` only).
