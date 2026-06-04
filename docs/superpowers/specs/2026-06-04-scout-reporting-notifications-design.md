# Scout reporting + notification layer — design

**Date:** 2026-06-04
**Status:** approved design, pre-implementation
**Branch:** `worktree-scout-notifications`
**Supersedes/extends:** `docs/NOTIFICATIONS.md` (§2 delivery semantics, §3 hardening plan)

## 1. What we're building

A real reporting + notification layer for the autonomous scout (`shortlist-scout`),
replacing today's bare `sendMessage` text dump. Each daily run produces:

1. **An inline chart image** ("the glance") sent to Telegram via `sendPhoto` — the
   composite-bars + sub-score-heatmap dashboard we prototyped, scaled to any N.
2. **A styled HTML deep-dive** sent as a Telegram **document** (`sendDocument`) and
   saved to disk — comprehensive raw metrics + Claude's full qualitative analysis,
   with no Telegram length limits.
3. **Hardened delivery** — chunking (4096 message / 1024 caption caps), retry/backoff
   with `Retry-After`, secret redaction — borrowing the *send-hardening* of the
   `/opt/oracle` Telegram client but **not** its long-running PTB command daemon
   (scout is a one-shot push).
4. **Persistent artifacts** under `scout/<date>/` and unchanged exit-code semantics.

### Goals
- Make the daily report decision-grade: surface tiers 2 (raw fundamentals) and 3
  (Claude analysis), which today's text report discards.
- Keep it **clean and extensible** — adding a report section, metric, or panel later
  is a localized change, not a shotgun edit across renderers.
- **Full control of rendering**, no heavyweight charting dep: Pillow for the raster
  glance, pure HTML/CSS for the document. matplotlib + numpy are **not** used.

### Non-goals
- No interactive/hosted dashboard, no web server, no Telegram command bot.
- No new scoring or discovery logic — this is presentation + delivery only.
- No change to the demo path's behavior (offline, prints text to stdout).

## 2. Data tiers (what the report can show)

All three already computed per run; today's report uses only a sliver.

| Tier | Source | Fields (examples) |
|------|--------|-------------------|
| **Scores** | `ScoreCard` | composite, 7 sub-scores, gates, flags (`value_trap`/`crowded_short`), confidence, scored/thin, sic_bucket, piotroski_f |
| **Raw fundamentals** | `ScoreCard.metrics` (`StockMetrics`) | price, market_cap, pe_ttm vs pe_median_5y, fcf_yield, peg, roe/roic, gross/net margin, debt_to_equity, revenue_cagr/eps_cagr, price_vs_200dma, rel_strength_6m, realized_vol, max_drawdown, rating_buy/hold/sell, target_median, insider_net_6m, insider_distinct_buyers |
| **Claude qualitative** | `QualitativeAssessment` (researched names only) | business_model_summary, moat, bull_case, bear_case, what_would_change_my_mind, risks[], red_flags[], management_capital_allocation, reconciliation[] |

## 3. Architecture: two layers + a curated glance

The core insight (from architecture review): a single flat view-model just relocates
shotgun surgery. Use **two layers**, and treat the PNG as a separate limited view.

**Layer A — view-model (`viewmodel.py`).** A renderer-agnostic, presentation-ready
snapshot of one run. Owns the messy work *once*: the per-ticker join of the three
tiers, None / masked-vs-missing / gated semantics, sorting. Values stay typed
(floats stay floats so the PNG can draw bars and HTML can format) — formatting is the
renderer's job, semantics are the view-model's. Pure data, no I/O, **no optional-dep
imports** (so demo + tests never pull Pillow).

**Layer B — sections (`sections.py`).** The report is an ordered registry of
sections. Each section knows how to (a) pull its fragment from the view-model,
(b) decide if it `applies(vm)`, and (c) render *itself* to HTML and to text. A new
report section = one new `Section` + one registry entry; the orchestrator, transport,
and other sections are untouched.

```python
class Section(Protocol):
    id: str
    title: str
    def applies(self, vm: ReportVM) -> bool: ...
    def render_html(self, vm: ReportVM, h: HtmlBuilder) -> str: ...
    def render_text(self, vm: ReportVM, detail: Detail) -> list[str]: ...
```

**HTML and text are the same sections at different verbosity** — a `Detail` enum
(`GLANCE` for the chunked Telegram text fallback, `FULL` for the document/disk text)
is passed in; sections decide how terse to be. This prevents the text fallback from
drifting from HTML (today's hand-maintained `render_message` is exactly that drift
risk). The chunked `sendMessage` fallback is just:
`"\n".join(line for s in SECTIONS if s.applies(vm) for line in s.render_text(vm, GLANCE))`.

**The PNG glance is NOT in the section registry.** It is `render_glance(vm, theme) ->
bytes`, reading only a few well-known view-model fields (per-row ticker, composite,
the 7 sub-scores, gate/thin tags). Raster layout does not compose like HTML flow;
future sections are added to HTML/text only, never to the chart. This asymmetry is
intentional and documented so a future contributor does not try to wire every section
into the canvas.

### Initial section set
1. `header` — session date, one-line top-N summary.
2. `leaderboard` — ranked composite table (mirrors the PNG bars, for the text/HTML
   path; the PNG itself is embedded above this in HTML).
3. `fundamentals` — per-leader raw-metrics table (tier 2), color-coded in HTML.
4. `research` — per-leader Claude analysis (tier 3); `applies()` false when no
   assessments. Bull/bear/what-would-change-my-mind/risks/red-flags.
5. `footer` — signals coverage line + discovery funnel + manifest notes.

## 4. Module layout (all under `src/shortlist/scout/`)

`report.py` becomes a thin **facade** so `daily.py` imports stay stable.

| File | Responsibility |
|------|----------------|
| `report/__init__.py` | Facade: `build_report(cards, manifest, *, assessments) -> ReportArtifacts` (`png: bytes\|None`, `html: str`, `text: str`). Re-exports `render_message`, which routes its `briefs` through a synthesized one-line assessment so the single research section renders them. |
| `report/viewmodel.py` | Build `ReportVM` from the three tiers + manifest; owns all None/abstention/sort logic. Pure, dep-free. |
| `report/sections.py` | Ordered `SECTIONS` registry + each `Section` (HTML + text methods). |
| `report/html.py` | `HtmlBuilder` (tag helpers + the single `esc()` choke-point) + embedded CSS theme + document assembly. Zero deps. |
| `report/png.py` | `render_glance(vm, theme) -> bytes` via Pillow. Lazy-imports Pillow; the **only** module that does. |
| `report/theme.py` | Palette constants + sub-score order/labels + `score_to_rgb(v: float\|None) -> (r,g,b)` colormap. One source of truth for HTML *and* PNG. Dep-free. |
| `notify.py` | `Notifier` transport seam (expand from current `send_telegram`). |

`report.py` (current module) is replaced by the `report/` package; its public name
`render_message` is preserved via the facade re-export.

## 5. The view-model sketch

`ReportVM` (field groups, not exhaustive):

```python
@dataclass
class LeaderVM:
    ticker: str
    name: str | None
    composite: float
    subscores: dict[str, float | None]   # quality..risk, None = masked/missing (distinguished below)
    masked: set[str]                      # legs abstained as inapplicable (sector) — render gray, not "missing"
    gates: list[str]
    flags: list[str]
    confidence: float | None
    thin: bool
    scored: bool
    metrics: MetricsVM                    # tier-2, pre-joined + None-resolved
    assessment: AssessmentVM | None       # tier-3, present iff researched

@dataclass
class ReportVM:
    session: date
    leaders: list[LeaderVM]               # already sorted (scored, composite) desc
    signals: list[SignalStatusVM]
    funnel: FunnelVM                       # raw/dedup/prefilter/screened/dropped
    notes: list[str]
```

`MetricsVM` carries the tier-2 numbers as typed values (e.g. `pe_ttm: float|None`,
`pe_median_5y: float|None`, `target_upside: float|None` sourced from the existing
`StockMetrics.upside_to_target()` method — DRY, not re-derived). `AssessmentVM` carries
`takeaway` (one-line TL;DR) plus bull/bear/cmm/risks/red_flags as already-extracted
strings/lists. The `takeaway` is what the GLANCE text shows, and is the slot the
back-compat `briefs` synthesize into. The view-model resolves the **masked-vs-missing**
distinction here so no renderer re-derives it (matches the scorer's abstention model:
a masked leg renders gray with a `·`, a missing-but-applicable leg renders distinctly).

## 6. HTML renderer

- Self-contained `.html`: `<!DOCTYPE html>`, embedded `<style>` (dark theme from
  `theme.py`), the Pillow PNG embedded at top as a base64 `data:` URI (one portable
  file, no broken-image links), then per-section markup.
- Per-leader fundamentals table: `<td>` cells with `background-color` from
  `score_to_rgb` (same palette as the chart); text color chosen by cell luminance.
- **Every** interpolated value — ticker, company name, Claude prose, signal `detail`
  — goes through a single `esc()` in `HtmlBuilder`. No raw f-string interpolation of
  model/issuer text into markup (injection surface: Claude- and issuer-authored text).
- Facts vs interpretation labeling carries over from the research layer's convention.

## 7. PNG glance renderer (Pillow)

Ports the mockup's `dashboard()` to Pillow reading the view-model. Concrete rules:

- **Fonts:** load system DejaVuSans/-Bold via `ImageFont.truetype(path, size)` from the
  known Ubuntu path, falling back to `ImageFont.load_default(size=)` (Pillow ≥10) then
  `load_default()`. (The review's "bundle a TTF" recommendation was relaxed: the target
  host is the Ubuntu VPS where DejaVu is present, and tests assert PNG *validity*, not
  pixels, so glyph determinism isn't required. No binary font is vendored. If cross-host
  determinism is later needed, vendor the TTF under `report/fonts/` and load via
  `importlib.resources` — deferred.)
- **Crispness:** choose final dimensions in **pixels** (e.g. 760px wide). Supersample
  at 2× then `img.resize(target, Image.LANCZOS)`. (No matplotlib `dpi` concept.)
- **Text measurement:** `draw.textbbox((0,0), s, font)` (Pillow ≥8). `textsize` is
  removed in Pillow 10 — do not use.
- **Layout:** explicit pixel y-cursor. Title → composite-bars panel (constant row
  height) → gap → heatmap panel. Height scales with N, chrome fixed (the
  mockup's proven model). **Empty-N renders an honest "No candidates" card**, never
  crashes (graceful-degradation guarantee).
- **Colors:** `score_to_rgb` from `theme.py` (piecewise-linear red→yellow→green over
  ~3 anchor stops, ~15 lines, no numpy). `None`/masked → neutral gray `#33404d`.
- **Heatmap cells:** filled `draw.rectangle` + centered text (`textbbox` centering);
  cell text color by fill luminance.
- Returns `bytes` (PNG). The facade catches `ImportError` (Pillow absent) → returns
  `png=None`; HTML omits the chart block; text fallback still sends.

## 8. Notifier transport + delivery policy

**Transport (`notify.py`)** — dumb, testable, httpx injected:

```python
class Notifier(Protocol):
    def configured(self) -> bool: ...
    def send_photo(self, png: bytes, caption: str) -> bool: ...
    def send_document(self, data: bytes, filename: str, caption: str) -> bool: ...
    def send_message(self, text: str) -> bool: ...   # chunks at 4096 internally

class TelegramNotifier:                               # implements Notifier
    def __init__(self, token, chat_id, client: httpx.Client | None = None): ...
```

- `configured()` is the **single source of truth** for the exit-code discriminator
  (replaces the two separate env reads in `daily.py`).
- Each `send_*` returns `bool`, swallows + **redacts** its own exceptions (the
  Telegram URL embeds the bot token — exactly what `redact_secrets` exists for).
- Retry/backoff with `Retry-After` lives in a private `_post`, mirroring
  `FMPProvider._get`'s 429 idiom (don't invent a second retry style).
- Caption ≤1024 enforced; message chunked ≤4096.

**Delivery policy (orchestration, not transport)** — a `deliver(notifier, artifacts)
-> DeliveryResult` function:

- Sequence: `send_photo(png, caption)` → `send_document(html, "scout-<date>.html")`.
  On any failure, fall back to `send_message(text)` + journal.
- `DeliveryResult(configured: bool, all_ok: bool, failures: list[str])` drives both
  the manifest note ("telegram delivery failed (configured)") and the exit code.

**Exit codes (preserved):** unconfigured → print fallback, **exit 0**;
configured-but-a-send-failed → note + **exit 2**. State-mutation ordering in
`daily.run()` (`mark_run_completed` before `record_screened`; manifest written after
delivery outcome known) is preserved exactly.

## 9. Orchestration wiring (`daily.py`)

- `_research_phase` returns, in addition to one-line `briefs`, the **full
  `QualitativeAssessment` per researched ticker** (`assessments: dict[str, ...]`).
  For cached hits (where in-memory `synthesis` is empty) it reconstructs the
  assessment from the on-disk JSON record. The HTML `research` section consumes these.
- `run()`: build `ReportArtifacts` (png/html/text) via the facade → if
  `notifier.configured()` deliver, else print text + write files → persist artifacts.

## 10. Persistence layout

Move from flat `scout/<date>.{txt,json}` to a per-run directory:

```
scout/<date>/
  dashboard.png     # the glance
  report.html       # the deep-dive
  report.txt        # text fallback (FULL detail)
  manifest.json     # RunManifest (unchanged schema)
```

## 11. Config

Minimal, under `config.yaml: scout.report` (all defaulted; absence = sane defaults):

```yaml
scout:
  report:
    chart: true            # render + send the PNG glance
    attach_html: true      # send the HTML document
    caption_top_n: 3       # names named in the photo caption
```

Telegram credentials stay in env (`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID`) per the
secrets convention — never in config.

## 12. Dependencies

- **Pillow** added as a scout extra: `uv sync --extra scout`. Lazy-imported only in
  `report/png.py` (same pattern as research/edgartools). ~3MB vs matplotlib's ~60MB;
  no numpy.
- HTML, view-model, sections, theme: **zero new deps**.
- No bundled fonts (system DejaVu + `load_default` fallback; see §7).

## 13. Graceful degradation

- Pillow absent → `png=None`; HTML drops the chart block; text still sends; exit
  unaffected.
- Research off/unavailable → `research` section `applies()` false; report shows
  scores + metrics only.
- Telegram unconfigured → write files, print text to journal, **exit 0**.
- Empty shortlist → "No candidates" PNG card + a minimal HTML/text report.
- **Demo path: prints text to stdout, no network/Pillow, exit 0.** (As-built note: the
  demo text is now rendered from the shared sections at GLANCE detail rather than the old
  `render_message` body, so the sub-score line was intentionally refreshed — full
  `Qual/Moat/Grow/Value/Mom/Insdr/Risk` labels and the value/momentum split instead of the
  old `Opp..Conf` form. No test pinned the old text; behavior/semantics are unchanged.)

## 14. Testing strategy

- **View-model (heaviest coverage, pure data):** None→display semantics,
  masked-vs-missing, sort order, three-tier join (researched ticker gets its thesis;
  non-researched does not), empty input.
- **HTML:** valid `str` starting `<!DOCTYPE html>`, parses under `html.parser`; every
  chosen ticker + gate present; research section present iff assessments passed;
  **escaping test** (feed `<script>`/`&`/`"` in ticker/name/thesis → assert escaped);
  `applies()` toggling.
- **PNG (invariants, never golden-diff):** returns `bytes` with PNG magic
  `\x89PNG\r\n\x1a\n`; `Image.open` succeeds, `.format == "PNG"`; height(10) >
  height(3); empty-N returns a valid small image, not a crash.
- **Transport/delivery:** `FakeNotifier` records calls → `deliver()` calls
  photo→document→(on failure)message; `configured()` False → exit 0, no sends;
  configured + a send False → note + exit 2; chunking (>4096 → ≥2 calls each ≤4096);
  redaction (exception string with a fake token → redacted output).
- **Back-compat:** characterize current `render_message` output, refactor to come
  from sections byte-identical; demo prints text and imports no Pillow.

## 15. Build order (sequencing)

Bottom-up; each layer testable before the next depends on it.

1. `theme.py` — palette + `score_to_rgb` + labels. Test colormap endpoints/midpoint/None.
2. `viewmodel.py` + `ReportVM` — the join + semantics. Heaviest tests. (low back-compat risk)
3. `sections.py` + text rendering → wire into the `report` facade reproducing the
   **current `render_message` output byte-identical** (characterize first). *Highest
   back-compat risk* — demo, persisted `.txt`, and existing assertions depend on it.
4. `html.py` — sections gain `render_html`; produce + save the document. (additive)
5. `report/png.py` — port the mockup to Pillow reading the VM. (parallel with 4)
6. `notify.py` `Notifier` + `deliver()` — expand transport, move configured/env logic
   out of `daily.py`. Preserve exit-code + state-ordering exactly.
7. Wire `daily.run()` — build all three artifacts, persist under `scout/<date>/`,
   deliver. Last (integrates everything).

## 16. Top risks & mitigations

1. **Text-fallback drift / back-compat (step 3).** Demo output, persisted `.txt`, and
   tests depend on the current text shape. → Pin a characterization test *first*,
   refactor to identical output, *then* extend.
2. **Pillow becoming an accidental hard dep.** If view-model/sections/theme import
   `png.py` at module load, demo + lean installs break. → Pillow imported only inside
   `png.py` functions; a CI run of `--demo` + tests with Pillow uninstalled.
3. **HTML injection.** Issuer/Claude text flows untrusted into markup. → single
   `esc()` choke-point in `HtmlBuilder` + the escaping test; no raw interpolation.
```
