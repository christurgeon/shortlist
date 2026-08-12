# PLAN — `/deep` freshness: brief cache key + Lazy-Prices similarity producer

**Date:** 2026-08-12 · **Status:** design, awaiting approval · **Scope:** `shortlist.research` only.
No scoring, gate, flag or composite semantics change. Origin: the external-review triage in
`TODO.md` §2a.

> **Spec location.** `docs/superpowers/specs/` is gitignored (`.gitignore:37`) and CLAUDE.md
> records two enablement artifacts already evaporating from there. This plan follows the
> repo's tracked `docs/PLAN_*.md` convention instead.

---

## 1. Goals

1. **A `/deep` brief must go stale when the world moves, and when the prompt moves.** Today it
   only goes stale when a filing lands.
2. **Surface the Lazy-Prices YoY text change inside `/deep`** at near-zero marginal fetch cost,
   using documents the brief already fetches.

## 2. Non-goals

- **Not** making the `filing_text_change` *flag* fire. See §5.3 — that requires collection-time
  filing parses and is explicitly out of scope.
- **Not** the memo/overlay pipeline split (the reviewer's structural fix, `TODO.md` §2a). This
  plan is the cheap correct-direction version of the same defect.
- **Not** 8-K exhibit text, statement notes, or peer bundles (`TODO.md` §2b/§2c).
- **No pruning of `research/`.** See §6.4.

---

## 3. Verified facts this design rests on

Every line below was checked against the code or the installed package, not assumed.

| Fact | Evidence |
|---|---|
| Brief cache key is filing accessions only | `research/filings.py:258` — `f"{tenk.accession}+{tenq_acc}"` |
| The pre-LLM short-circuit keys on it | `research/__init__.py:55` — `report.is_cached(card.ticker, bundle.cache_key, root)` |
| `report.write` keys on the assessment's copy | `report.py:139` — `key = a.cache_key or a.filing_accession` |
| Nothing outside `research/` reconstructs brief filenames | bot `/deep` (`telegram.py:321-350`) → `phase.py:28-119` treats `brief_path` as opaque, derives the JSON sibling via `with_suffix(".json")` |
| `assessment.cache_key` has no other reader | persisted by `asdict` at `report.py:145`, never parsed back (`phase.py:112-137` reads `synthesis`/`thesis.takeaway`/`summary` only) |
| **The bot never refreshes** | `phase.py:81` hardcodes `refresh=False`; only CLI `--refresh` (`screen.py:128,193,214`) can force one |
| `card.metrics` is always set on the scoring path | `scoring.py:852-859` |
| …but **test cards are duck-typed stubs with no `.metrics`** | `tests/research/test_enrich.py:6-15` |
| `macro` can be `None` | `screen.py:187` (`--demo`), `macro.py:69` (`fetch_macro -> MacroContext | None`, no `FRED_API_KEY`) |
| `MacroContext.regime` is a stable label | `macro.py:24-50` — `"risk-on" | "neutral" | "risk-off"` |
| `filing_events` / `insider_recent` shapes | `models.py:114-120`; `{form, filed, accession, url, items}` (`data/models.py:289-298`) and `{date, name, role, kind, value}` (`data/bridge.py:205-207`) |
| `upside_to_target` / `pe_vs_history` are **methods** | `models.py:199,204` — landmine; neither is hashed |
| `fetch_bundle` already fetches + parses the prior-year 10-K | `filings.py:255` → `_prior_year_risk_factors` |
| `filing_text_similarity` has **no setter anywhere in `src/`** | grep: only the dataclass field (`models.py:194`) and consumers |
| `filing_text_change()` has **no caller** | grep across `src/` |
| Flags are computed inside `score()`, before research runs | `scoring.py:809` inside `score()`; `screen.py:188` then `screen.py:193` |
| `TenQ` has **no** `risk_factors` property | edgartools 5.33.0 — only `TenK`/`TwentyF`/`FortyF` (`ten_k.py:297`) |
| edgartools is pinned loosely | `pyproject.toml:18` `edgartools>=3.0`; `uv.lock` resolves 5.33.0 |

---

## 4. Workstream A — widen the brief cache key

### 4.1 The invariant

**The wide key MUST be computed before `research/__init__.py:55`.** If it is computed only
before `report.write`, the pre-LLM short-circuit keeps matching the narrow accession key, no
regeneration ever triggers, and every legacy brief is treated as current forever. A test pins
this by asserting the LLM runner is invoked when only the context changed.

### 4.2 New module: `research/cachekey.py`

Import-time `inspect.getsource` is its only I/O — no network, no edgartools. Three public names:

```
PROMPT_FINGERPRINT: str          # 8 hex chars, computed once at import
def context_digest(card, macro, config) -> str      # 8 hex chars
def brief_key(bundle, card, *, macro=None, config=None, today=None) -> str
```

Key shape: `{tenk_acc}+{tenq_acc}-p{prompt8}-c{ctx8}-{bucket}`

**`PROMPT_FINGERPRINT`** — sha1, truncated to 8, over the source of **every module that shapes
the prompt or the guards**, plus a canonical `repr` of the `research` config block.

Modules hashed (`_PROMPT_MODULES`): `assess`, `models` (it holds `SCHEMA_HINT`, concatenated
into the system prompt at `assess.py:87`), `reverse_dcf`, `coverage_caveat`, `proxy`,
`gov_contracts`, `lobbying`, `earnings`, `riskdiff` (its output is `bundle.added_risks_text`,
which reaches both the prompt and the haystack).

> **Why a module SET.** Two smaller designs were considered and both fail:
> `getsource(_build_user_prompt) + getsource(apply_guards)` misses the callees that produce most
> of the prompt (`_quant_context`, `_insider_line`, `_macro_line`) because `inspect.getsource`
> does not follow calls. Hashing `assess.py` alone still misses `SCHEMA_HINT` in `models.py` and
> every context-line renderer that lives in its own module — `reverse_dcf.format_line`,
> `proxy.context_line`, `gov_contracts.context_line`, `lobbying.context_line`,
> `earnings.context_line`, `coverage_caveat.coverage_caveats`. Editing the response schema or any
> one of those renderers changes every brief; under either narrower design it would invalidate
> nothing, which is precisely the defect this module exists to close.

**Config is hashed too**, because prompt-shaping values live in `config.yaml`, not only in
source: `research.max_chars` (applied by `cap_bundle`, `__init__.py:63`, and named explicitly in
`TODO.md` §2a), `research.model`, `max_risks` / `max_red_flags` / `max_conflicts` /
`max_falsifiers` (rendered verbatim at `assess.py:332-336`), and the `enabled` switches for
`screening_call` / `proxy` / `insider_detail` / `macro` / `risk_diff`. The whole `research` block
is hashed as a canonical `repr`, **less `output_root`** (a filesystem path, not prompt content)
and **less `cache`** (its values already move the key mechanically).

`getsource` is wrapped — on any failure (frozen or zipped installs) it falls back to the
module-level constant `_FINGERPRINT_FALLBACK = "00000000"`, preserving the 8-char key shape.
That path is tested.

**`context_digest`** — sha1 over an explicit, ordered, **bucketed** materiality tuple. Bucketing
is the whole point: hashing raw price would make every invocation a cache miss and defeat
caching entirely.

> **The completeness rule.** Everything `_quant_context` renders from the card belongs in this
> table. An earlier draft hashed roughly half of it, which meant a new federal contract award —
> or any change to `pe_ttm`, `gross_margin`, `net_income`, `diluted_shares` … — produced a
> materially different prompt under an identical key. The three auxiliary lines are therefore
> hashed as their **rendered strings**, so a future field added inside one of them is covered
> automatically rather than silently missed.

| Field | Bucket rule | Rationale |
|---|---|---|
| `price`, `market_cap` | `floor(log(v) / log(1 + band))`, `band = research.cache.price_band_pct` (default `0.03`); `v is None or v <= 0` ⇒ the `None` sentinel, never `log()` | rebuild on a ≈3% move, not on every tick |
| `quality…risk` sub-scores, `composite` | round to nearest 5 | the prompt prints them as integers |
| `confidence` | round to 0.05 | printed to 2dp |
| `gates`, `flags` | exact, sorted | any change is material by definition |
| `sic_bucket` | exact | rendered at `assess.py:418-419` |
| `filing_events` | exact, sorted `(form, items, filed)` | a new 8-K/13D **must** bust the cache |
| `insider_recent` | trade count + `round(net_value / 1e5)` | new Form 4s are material; cents are not |
| short interest | `short_pct_outstanding` to 0.001, `days_to_cover` to 0.5, `short_interest_rising` exact | |
| `revenue_cagr`, `fcf_cagr`, `eps_cagr`, `revenue_growth_persistence`, `gross_margin`, `net_margin`, `roic`, `debt_to_equity`, `interest_coverage` | 3 significant figures | the full `Fundamentals:` line (`assess.py:424-429`); catches a **statements repair with no new filing** |
| `pe_ttm`, `pe_median_5y`, `fcf_yield`, `peg` | 3 significant figures | the `Valuation:` line (`assess.py:436-441`) |
| `financial_series` | per row, all rendered columns to 3sf: `fiscal_year`, `period_end`, `revenue`, `gross_profit`, `net_income`, `operating_cash_flow`, `free_cash_flow`, `total_debt`, `diluted_eps`, `diluted_shares` | the prompt renders the whole table (`assess.py:349-366`) **and** derives the reverse-DCF implied growth from it |
| gov-contract / lobbying / earnings lines | the **rendered string** from each `context_line(m, cfg)` | pure and network-free; covers their internals by construction (`assess.py:460-468`) |
| macro | `macro.regime` or `"none"` | `None` is a real production state |

Everything is read via `getattr(..., None)` with a `None` sentinel in the tuple, because
`card` may be a stub without `.metrics` (§3) and every metric is `Optional`.

**Deliberately excluded:** DEF 14A proxy facts. They are fetched *inside* `assess`
(`assess.py:594-598`), so hashing them would force a network call on every cache check. Proxy
data moves annually; the day bucket covers it. This exclusion is a comment in the code.

**`bucket`** — `research.cache.max_age_days` (default `1`). `days_since_epoch // max_age_days`,
rendered as the ISO date of the bucket start. **`0` disables** time-based invalidation entirely
(byte-stable key), which is the escape hatch for anyone who wants pure content addressing.

### 4.3 Wiring

`_enrich_card` computes `key = cachekey.brief_key(bundle, card, macro=macro, config=config)`
immediately after the `bundle is None` check, then uses `key` for **both** `is_cached` and
`brief_path`, and sets `assessment.cache_key = key` before `report.write`. `assess()` is
untouched — it keeps writing `bundle.cache_key` at `assess.py:643`, which `_enrich_card`
overwrites. `FilingBundle.cache_key` keeps its current filing-only meaning and its literal
`+`-join format (pinned by the live test at `test_filings_integration.py:20`).

### 4.4 Config

```yaml
research:
  cache:
    max_age_days: 1        # 0 disables time-based invalidation
    price_band_pct: 0.03   # rebuild when price/mcap moves ~3%
```

Absent block ⇒ these defaults, so an untouched `config.yaml` still gets the fix.

---

## 5. Workstream B — produce the Lazy-Prices similarity for `/deep`

### 5.1 What exists and what is missing

Downstream is complete and configured ON: the flag (`scoring.py:709-717`), config
(`config.yaml:244`), glossary (`bot/glossary.py:281`), bot theme (`report/theme.py:27`), tests
(`tests/test_filing_text_change_flag.py`), the field (`models.py:194`) and the computation
(`research/filings.py:148` + `research/textsim.py`). **Nothing produces the input**, and
`filing_text_change()` has no caller. It is a producer-less feature.

### 5.2 The cheap producer

`fetch_bundle` already fetches, parses and discards most of the prior-year 10-K
(`filings.py:255`). Change `_prior_year_risk_factors` to `_prior_year_sections`, returning
`(risk_factors, mda)` from the **same already-parsed filing object** — one extra attribute read,
**no extra network request**. Then compute
`textsim.combined_similarity(cur_risk, prior_risk, cur_mda, prior_mda)` from documents already
in hand and carry it on `FilingBundle` as `text_similarity: Optional[float]`.

Surfacing, both prompt-only (never the grounding haystack — it is a computed number, not filing
text, and must not be quotable as a filing fact):

- one context line, e.g. `Filing-text change (Lazy Prices): FY2025 10-K risk-factor + MD&A
  language is 38% rewritten vs FY2024 (cosine 0.62; low similarity has been associated with
  weaker forward returns — context only, not a filing fact).`
- one rendered line in the brief so it survives outside the prompt.

Config `research.text_similarity.enabled`, default **true** whether or not the block is present.
`enabled: false` is the byte-identical-to-today escape hatch — note this is the one place in
this plan where an *absent* config block is not a no-op, which is deliberate: a producer nobody
switches on is how this feature ended up dead the first time.

### 5.3 What this deliberately does NOT do

**The `filing_text_change` flag stays dormant.** `check_flags` runs inside `score()`
(`scoring.py:809`) during `run_harness` (`screen.py:188`); research runs afterwards
(`screen.py:193`). A research-layer producer is structurally too late. Making the flag fire
needs the similarity at *collection* time — two full 10-K parses per ticker per screen, which
is not viable for a 10-ticker `/screen`. `config.yaml:241-243` already documents the flag as a
no-op on a plain screen; this plan does not change that, and **must not be described as
"activating the flag."**

### 5.4 Honest status of the signal

Ships as a **defensible prior, not a measured edge**: Cohen-Malloy-Nguyen (2020) is the source,
and `TODO.md` §3 records that this signal can never be validated on the snapshot-replay path
(full filing text is deliberately absent from snapshots). It is a context line on a human-read
brief, touches no score, and is labelled as interpretation where it surfaces.

### 5.5 Follow-on, not in scope

The 10-Q arm of the similarity is broken independently: `_filing_sections` (`filings.py:112`)
reads `risk_factors` off a `TenQ`, which has no such property, so a 10-Q comparison silently
comes out MD&A-only. Fixing it needs Part II Item 1A via
`get_item_with_part("Part II", "Item 1A")` — verified to work on edgartools 5.33.0. Dormant
today (no caller), so it is recorded in `TODO.md`, not built here.

---

## 6. Risks and how each is handled

1. **Whole-corpus regeneration.** Every existing brief's key changes ⇒ one rebuild per ticker on
   next `/deep`. This is the intended effect of the fix, but it is real LLM spend. Called out in
   the PR description; no migration (briefs are a gitignored cache).
2. **A too-tight band burns tokens.** A 3% price band plus a 1-day bucket is a prior, not a
   measurement. If rebuild rate proves annoying, `price_band_pct` and `max_age_days` are the
   two dials, both config.
3. **edgartools drift.** §5.5's Part II keys and the existing 10-Q MD&A extraction are internal
   parser structure behind a `>=3.0` pin. Unit doubles cannot catch upstream drift — CLAUDE.md
   records `standard_concept` breaking extraction exactly this way. Workstream B adds no new
   edgartools surface (it reads attributes already read today), so this risk is unchanged by
   this plan.
4. **Unbounded `research/` growth.** No pruning exists and none is added. Be precise about the
   rate: with `max_age_days: 1`, running `/deep TSLA` on two consecutive days writes a second
   `.md`+`.json` pair **even if nothing moved**, because the day bucket alone changed. Growth is
   therefore bounded by *invocations*, not by material change — one pair per ticker per day you
   actually ask. At a few KB each that is immaterial for years; `max_age_days: 0` removes even
   that. Documented, not engineered.
5. **`getsource` unavailable.** Falls back to a constant fingerprint; the fix degrades to
   "context + day bucket still work, prompt edits no longer self-invalidate". Tested.

---

## 7. Test plan

**`tests/research/test_cachekey.py`** (new)

- identical inputs ⇒ identical key (determinism, run twice in-process)
- price move **inside** the band ⇒ key unchanged; **outside** ⇒ key changes
- new gate / new flag / new `filing_events` entry / extra insider trade ⇒ key changes
- `macro=None` vs a `MacroContext` ⇒ different, neither raises
- **card stub with no `.metrics`** ⇒ no raise, stable key (mirrors `test_enrich.py:6-15`)
- `max_age_days: 0` ⇒ key stable across simulated day rollover (inject `today`)
- `max_age_days: 1` ⇒ key changes across a simulated day rollover
- `PROMPT_FINGERPRINT` is 8 hex chars and stable within a process; the `getsource`-failure
  fallback path returns the constant

**`tests/research/test_enrich.py`** (extend)

- **the invariant test:** a brief cached under the *old narrow* key does **not** short-circuit
  `_enrich_card` — the runner is called. This is the test that would fail if the key were
  computed only before `report.write`.
- a materially-changed card ⇒ runner called; an unchanged card ⇒ runner not called (extends
  `test_enrich_uses_cache_unless_refresh`)

**Verified to need NO change:** `test_report.py:36,80,92` and `test_models.py:161,187` exercise
`report.py`/`models.py` directly with hand-supplied keys, and neither module changes here
(baseline: `uv run pytest tests/research/ -q` → 175 passed, 1 skipped). The live
`test_filings_integration.py:20` asserts `FilingBundle.cache_key`'s `+` join, also unchanged.

**One test goes green-but-vacuous and must be rewritten anyway:**
`test_enrich_new_10q_invalidates_cache` (`test_enrich.py:72`) pre-seeds `acc-A+q1` and fetches
`acc-A+q2`. Under the wide key the pre-seeded file can never match *any* wide key, so the test
passes for the wrong reason and no longer tests 10-Q invalidation at all. It must be re-derived
through `cachekey.brief_key`, not left green.

**Workstream B:** `_prior_year_sections` returns both sections from one filing object (fake
double, following `tests/research/test_filings.py:83-98`); similarity present ⇒ context line
rendered and **absent from `bundle.haystack()`**; `enabled: false` and a `None` similarity ⇒
byte-identical prompt.

**Full-suite guard:** `uv run pytest` must be green, and `uv run shortlist --demo` must still
run offline (the deploy smoke test is read-only and must stay that way).

---

## 8. Files touched

| File | Change |
|---|---|
| `src/shortlist/research/cachekey.py` | **new** — pure key/digest module |
| `src/shortlist/research/__init__.py` | compute wide key before the `is_cached` check; set it on the assessment |
| `src/shortlist/research/filings.py` | `_prior_year_sections`; `text_similarity` on the bundle; fix the stale `markdown=True` comment |
| `src/shortlist/research/models.py` | `FilingBundle.text_similarity` |
| `src/shortlist/research/assess.py` | similarity context line (prompt-only) |
| `src/shortlist/research/report.py` | render the similarity line |
| `config.yaml` | `research.cache.*`, `research.text_similarity.enabled` |
| `tests/research/test_cachekey.py` | new |
| `tests/research/test_enrich.py`, `test_report.py`, `test_models.py`, `test_filings.py` | updated / extended |
| `TODO.md` | close the §2a cache entry; record the producer-less flag + the 10-Q `risk_factors` gap |
| `CLAUDE.md` | one line: brief cache key is context-aware; the flag remains screen-path dormant |
