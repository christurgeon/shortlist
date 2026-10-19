# Design docs

These docs record design intent at the time of writing. Their file and line references are not maintained; read the code or the current-behaviour docs in `docs/` for how the system works.

- [PLAN_DEEP_FRESHNESS.md](PLAN_DEEP_FRESHNESS.md) — `/deep` freshness: brief cache key and Lazy-Prices similarity producer
- [PLAN_DEEP_FRESHNESS_TASKS.md](PLAN_DEEP_FRESHNESS_TASKS.md) — Implementation tasks for the `/deep` freshness plan
- [PLAN_EDGAR_DILUTED_SHARES.md](PLAN_EDGAR_DILUTED_SHARES.md) — EDGAR diluted-shares/EPS extraction: concept-matching fix
- [PLAN_EDGAR_ROOT_CAUSE_B.md](PLAN_EDGAR_ROOT_CAUSE_B.md) — EDGAR root cause B: recover `diluted_shares` from the companyconcept API
- [PLAN_INVENTORY_DECOMPOSITION.md](PLAN_INVENTORY_DECOMPOSITION.md) — Inventory context line, pre-clamp stance, and textsim retirement
- [PLAN_STATEMENTS_MERGE.md](PLAN_STATEMENTS_MERGE.md) — Implementation plan for the Statements year-joined merge
- [POSITION_MONITOR.md](POSITION_MONITOR.md) — Position monitor design (v1); its 8-K alerting is not built
- [STATEMENTS_MERGE.md](STATEMENTS_MERGE.md) — Statements merge: year-joined backfill design
