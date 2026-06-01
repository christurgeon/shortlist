# Coverage diagnostic — design

**Date:** 2026-05-31
**Status:** Approved (pending spec review)

## Problem

When a provider can't supply data for a ticker, the affected sub-scores come back
`null` and the cause is invisible in the output — a consumer (a human, a tool, or
a future Claude session) has to *infer* the cause from absence. The most common
case: FMP's free plan gates many symbols per-symbol with a `402` "Special
Endpoint" (e.g. AXON, MELI, ISRG, SCHW, TMO), which nulls the entire `value` axis
(`value`, `upside_to_target`) because PE-vs-history, FCF yield, and analyst-target
upside all live on FMP. A second, quieter mode exists: some symbols return an
empty `200` rather than a `402`, so FMP raises no exception but contributes no
fields.

Today the only signal is the raw `! fmp failed for SCHW: 402 ...` line on stderr
(for the raising case only) — it is not machine-readable and does not connect the
failure to its downstream consequence.

## Goal

Surface *why* data is thin/null for a ticker, both:
- **machine-readably** in `--json` (a `coverage` block per card), and
- **human-readably** on stderr (a `Coverage notes` summary),

covering both gating modes (`402` and empty-`200`), without coupling the
diagnostic to scoring internals and without claiming causation we cannot prove.

## Non-goals (YAGNI)

- No CSV schema change (nested coverage doesn't fit the flat columns).
- No change to the rich table (the stderr summary covers the human read).
- No new config knobs.
- No hand-maintained `field → provider → subscore` dependency map. (See "Attribution
  decision" — this is a deliberate rejection of the higher-coupling approach.)

## Attribution decision

Two candidate ways to populate the `unavailable` list were considered:

- **Attributed** — list only the null sub-scores whose inputs come from the failed
  provider. Rejected: requires a `field → provider → subscore` dependency map that
  duplicates knowledge in `scoring.py`/providers and silently drifts when scoring
  changes (the same coupling failure mode CLAUDE.md warns about for the insider
  merge). A diagnostic that lies after a refactor is worse than none.
- **Factual (chosen)** — structured fields report only facts; the interpretive
  `note` carries causation. This mirrors the research layer's established principle
  ("facts are quote-verified; interpretive prose is labeled"). Real per-field
  attribution, where needed, is derived from the existing `metrics.sources` audit
  trail rather than a hand-coded map — which also lets us detect the empty-`200`
  mode for free.

## Data model (`src/shortlist/models.py`)

Add a `Coverage` dataclass and an optional field on `ScoreCard`:

```python
@dataclass
class Coverage:
    providers: dict[str, str]     # provider name -> status (see taxonomy)
    unavailable: list[str]        # output fields that are null on this card (fact)
    note: Optional[str] = None    # interpretive; set for recognized patterns
```

`ScoreCard` gains: `coverage: Optional[Coverage] = None` (default keeps existing
construction paths and tests unaffected).

### Status taxonomy

Per provider, per ticker:

| Status      | Meaning                                                              |
|-------------|---------------------------------------------------------------------|
| `ok`        | Provider was attempted, did not raise, and supplied ≥1 field        |
| `gated_402` | Provider raised an HTTP `402` (paid/gated symbol)                    |
| `empty`     | Provider was attempted, did not raise, but supplied **zero** fields  |
| `error`     | Provider raised any other exception (network, other HTTP, SDK, etc.) |

A provider that was **globally skipped** before the ticker loop (missing key /
uninstalled SDK) is *not* represented per-ticker — that already prints
`! skipping provider '<name>'` to stderr at startup, and the per-ticker `coverage`
reflects only providers actually attempted for that ticker.

## New module (`src/shortlist/coverage.py`)

Isolated, dependency-light, pure functions — easy to unit-test.

- `classify_failure(exc) -> str`
  Returns `"gated_402"` when the exception carries an HTTP `402`, else `"error"`.
  Detection is via `getattr(getattr(exc, "response", None), "status_code", None)
  == 402` — i.e. a status-code check, **not** string parsing, and **no** hard
  dependency on `requests` (the FMP/Finnhub providers raise `requests.HTTPError`,
  which exposes `.response.status_code`).

- `build_coverage(outcomes, card) -> Optional[Coverage]`
  Inputs: `outcomes: dict[str, str]` (provider name -> raise-time status: `"ok"`
  for success, else the `classify_failure` result) and the scored `card`.
  Logic:
  1. Reclassify any `"ok"` provider that contributed **zero** fields to the merged
     metrics (counted from `card.metrics.sources`) as `"empty"`. Note: the value in
     `sources` is `_provider_of(m)` (merge.py), the most common provider name in
     that source's own `sources` dict — reliable because each `StockMetrics` from a
     single provider has a uniform `sources` dict, but indirect; count by matching
     the provider `name`.
  2. Build `unavailable` = the output fields among
     `{quality, moat, momentum, value, insider, upside_to_target}` that are `None`
     on the card. (`composite`/`opportunity` are excluded — always derived/present.)
     **Access is heterogeneous:** `quality/moat/momentum/value/insider` are
     attributes on the *card* (`getattr(card, name)`), but `upside_to_target` is a
     **method on `card.metrics`** (`card.metrics.upside_to_target()`), not a card
     attribute. The whole derivation must guard `card.metrics is None` (treat all
     metrics-derived fields as unavailable in that case).
  3. Set `note` when any provider status is `gated_402` or `empty`. When the
     affected provider is FMP, use FMP-specific wording naming the value axis and
     the Starter-tier fix; otherwise a generic one-line note. The branch keys off
     the literal provider name `"fmp"` (e.g. `outcomes.get("fmp") in {"gated_402",
     "empty"}`) — this string is **load-bearing and coupled to the registry name**
     (`fmp.py: name = "fmp"`); document it at the call site.
  4. Return `None` when every provider is `ok` (clean cards stay clean; presence of
     `coverage` is itself meaningful).

- `coverage_note_line(ticker, cov) -> str`
  One-line stderr rendering, e.g.
  `SCHW   fmp gated (402) -> value, upside_to_target unavailable`.

## Wiring (`src/shortlist/screen.py`)

- `run()` — initialize `outcomes = {}` **per ticker** (alongside `per_provider = []`
  at the top of each ticker iteration, so outcomes never leak across tickers), then
  record outcomes inside the provider loop:
  ```python
  for t in tickers:
      per_provider = []
      outcomes: dict[str, str] = {}        # reset per ticker — must not leak
      for p in providers:
          try:
              per_provider.append(p.fetch(t))
              outcomes[p.name] = "ok"
          except Exception as e:
              outcomes[p.name] = classify_failure(e)
              print(f"  ! {p.name} failed for {t}: {redact_secrets(e)}", file=sys.stderr)  # unchanged
      if not per_provider:
          continue
      card = score(merge(per_provider), config)
      card.coverage = build_coverage(outcomes, card)   # may be None
      cards.append(card)
  ```

- `_card_dict()` — add `"coverage"` to the dict only when `c.coverage is not None`
  (same conditional pattern as `research_path`). Serialize the dataclass to a
  plain dict (`providers`, `unavailable`, and `note` only when set).

- `main()` — after `run()`, when any card carries coverage, print a
  `Coverage notes` block to **stderr** (in both table and `--json` modes, so it
  never contaminates `--json` stdout — matching the research-phase pattern).

## Data flow

```
run(): for each ticker
  for each provider p:
    p.fetch(t) ok      -> outcomes[p.name] = "ok"
    p.fetch(t) raises  -> outcomes[p.name] = classify_failure(e); print raw stderr line
  card = score(merge(per_provider), config)
  card.coverage = build_coverage(outcomes, card)   # may be None
main():
  --json  -> _card_dict emits "coverage" when present  (stdout)
  always  -> Coverage notes block when any card has coverage  (stderr)
```

## Example output

JSON (per card):

```json
{
  "ticker": "SCHW",
  "value": null,
  "insider": 10.8,
  "coverage": {
    "providers": {"fmp": "gated_402", "finnhub": "ok", "edgar": "ok"},
    "unavailable": ["value", "upside_to_target"],
    "note": "FMP gated this symbol (402); value axis (PE-vs-history, FCF yield, target upside) needs FMP Starter tier"
  }
}
```

stderr:

```
Coverage notes
  SCHW   fmp gated (402) -> value, upside_to_target unavailable
```

## Testing (TDD)

New `tests/test_coverage.py`:
- `classify_failure`: 402 HTTPError -> `"gated_402"`; non-402 HTTPError -> `"error"`;
  exception with no `.response` -> `"error"`.
- `build_coverage`:
  - gated_402 outcome + null `value` -> providers map has `gated_402`,
    `unavailable` includes `value` **and** `upside_to_target`, `note` mentions
    FMP/Starter.
  - `"ok"` provider that supplied zero fields (no entry for it in `metrics.sources`)
    -> reclassified to `"empty"`.
  - `card.metrics is None` -> does not raise; metrics-derived fields
    (`upside_to_target`) reported unavailable.
  - all-`ok` outcomes (each provider contributed ≥1 field) -> returns `None`.
- `run()` leak guard: a ticker whose FMP is gated followed by a fully-`ok` ticker ->
  the second ticker's `coverage` is `None` (outcomes did not leak).
- `_card_dict` (extend `tests/`): emits `coverage` when present; omits when `None`.

All new/changed logic lands behind a failing test first. Existing suite (83 tests)
must stay green; the new `ScoreCard.coverage` field defaults to `None` so current
construction paths are unaffected.

## Files touched

- `src/shortlist/models.py` — `Coverage` dataclass + `ScoreCard.coverage` field.
- `src/shortlist/coverage.py` — new: `classify_failure`, `build_coverage`,
  `coverage_note_line`.
- `src/shortlist/screen.py` — outcome capture in `run()`, `_card_dict` emission,
  stderr `Coverage notes` block in `main()`.
- `tests/test_coverage.py` — new unit tests.
- (optional) `tests/test_scoring.py` or a screen test — `_card_dict` emission case.

## Documentation follow-up (post-implementation)

Once shipped, add a short note to the `/run` skill that a `coverage` block now
makes the FMP-gating cause explicit (replacing "inferred from absence"), and
mention the `coverage` JSON field in CLAUDE.md's screener data-flow section.
```
