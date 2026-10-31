# TODO — open follow-ups

**A working set of open work, not a session journal** — when an entry's work ships, delete it
rather than marking it done. The durable record is the code and its docstrings (behaviour +
landmines), `CLAUDE.md` (policy + where the authority lives), `docs/audits/` (evidence) and git
history; a resolved entry left here is pure cost. This file reached 2,133 lines by 2026-08-08
because nobody owned removing anything. **The goal is to empty it and delete it.**

Nothing settled lives here any more. Closed verdicts, and the positions this repo has
considered and declined, are in **`docs/audits/README.md`** — read that before opening a work
item, so a closed question is not reopened from scratch.

Section numbers are stable anchors: `src/`, `tests/` and `config.yaml` cite them (`TODO.md
§2a`), so a retired section leaves a GAP rather than renumbering the rest.

See `docs/PREDICTIVE_SIGNALS_RESEARCH.md` for signal designs and `docs/ASSESSMENT_GAPS.md` for
the scoring roadmap.

> **HOW TO PRIORITISE ANYTHING BELOW.** The bar is **"surface interesting stocks the user
> evaluates, and passes to `/deep` when they want a closer look."** *It is fine for a signal to
> have no measurable edge* — that is `CLAUDE.md`'s design premise, not a defect.
>
> **Discovery is the user's own research**, feeding `/screen` and `/deep`. Work that makes a
> *supplied* name easier to judge outranks work that measures forward returns.

---

# 5. Discoverability (2026-10-19)

- **Publish to PyPI** so `uvx shortlist --demo` works without a clone. The name `shortlist`
  returned 404 on PyPI on 2026-10-19; check again before you claim it. `pyproject.toml` already
  has the metadata. Still needed: a trusted-publishing workflow and a README install line.
  `config.yaml` is read from the repo root (`screen.py --config` default), so an installed
  wheel needs a packaged default config first.
- **Add a screenshot** of the Telegram PNG dashboard to the README, and set it as the GitHub
  social preview image (Settings → Social preview; there is no API for it).
- **Submit to awesome-quant** (`wilsonfreitas/awesome-quant`, Python → fundamental analysis).
  Do this after the PyPI release so the entry can show an install command.

**Status:** not started. Each step publishes something outside the repo, so each needs the
owner to approve it.

---

# 2. `/deep`

## 2d. ROIC-persistence section — reference-table refresh

- **A reference-table refresh builder, due before fiscal-2027 10-Ks (about September 2027).**
  After that every name abstains as `reference_out_of_date`. Do not run `probe_durability.py
  fetch` to make one, and do not raise `max_table_gap_years`: `docs/RESEARCH.md` → The ROIC-persistence
  section, and `docs/audits/2026-10-08-moat-durability-phase1.md` → The reference year.

---

# 3. Measurement (backtest + snapshot replay)

Ordering note: everything here answers an alpha question, which the prioritisation rule at the
top ranks *below* work on judging a supplied name. Take these when they are cheap or when they
unblock something.

## Shipped weight still unvalidated: `insider` (2026-09-25)

- **`insider` (weight 0.135) and the `heavy_insider_selling` gate.** Neither has ever been
  measured: insider history is not in companyfacts, so the only route is snapshot replay. Both
  are driven mostly by *sales* (Finnhub MSPR), the side the literature finds uninformative
  (Lakonishok-Lee 2001; Cohen-Malloy-Pomorski 2012 — purchases predict, routine sales do not).
  On the 2026-09-24 store the gate removed **6 of 42** large caps, including NVDA, the top
  composite at 77.4, plus COST, CSCO and QCOM; `insider_sentiment` sat at the 0 clamp for 17
  of 42. The candidate change is weight down and gate → flag, but that is an argument, and the
  register's rule needs evidence first.

**Status:** blocked on calendar time: the store holds 42 names and ~3 non-overlapping monthly
periods, far below the 24-period floor.

## Waiting on calendar time

- **SUE** is blocked on calendar time only — a verdict with ≥8 non-overlapping blocks is a
  late-2026-into-2027 proposition. Keep accumulating.

---

# 4. Data layer

## FMP quota is over-subscribed — a config-or-money decision, not a build

Accumulate (42 tickers) alone runs ~550 calls/day against a 250/day free limit, which is why
**23 of 60 store dates have ZERO fmp-won statements** (re-measured 2026-08-21 over 2,146
snapshots; FMP wins only 672 of them). This is the **sole** driver of the EDGAR-only
`operating_income` gap — 100% of fmp-won snapshots carry it against 62.8% of EDGAR-only ones,
and no EDGAR-side change can close more than 12% of that
(`docs/audits/2026-08-21-operating-income-edgar-gap.md`). EDGAR supplies ~100% of production
statements. Options: drop `--max-tickers` to ~18; remove `fmp` from the accumulate chain
(the store then loses `gross_profit`, which only FMP supplies); or paid **Starter**
(~$14–20/mo).

The free window is **not calendar-UTC-day aligned** (measured 2026-07-31: still "Limit Reach"
42 min after UTC midnight, drained by our own 21:30 accumulate timer). Retry probes in the
**mid-day UTC window**.
