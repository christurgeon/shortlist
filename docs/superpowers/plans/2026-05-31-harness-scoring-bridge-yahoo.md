# Harness Scoring Bridge + Yahoo Price Source — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the harness `TickerSnapshot` scoreable via a pure bridge, and add a keyless Yahoo OHLCV source so momentum/risk are computed by us and survive FMP's per-symbol gating, exposed through an opt-in `shortlist --engine harness`.

**Architecture:** A pure `snapshot_to_metrics()` bridge maps the six `TickerSnapshot` sections onto the flat `StockMetrics` that `scoring.score()` already consumes (deriving `gross_margin_stability`/`fcf_positive`, leaving `pe_median_5y`/`roic_5y_avg` as accepted `None` parity gaps). A new async `YahooSource` (day-cached, merged ahead of FMP) fills `Price` momentum/risk. A thin `run_harness()` branch behind `--engine harness` keeps the proven screener path untouched.

**Tech Stack:** Python 3, `httpx` (async, already a dep), `dataclasses`, `pytest`, `uv`.

Spec: `docs/superpowers/specs/2026-05-31-harness-scoring-bridge-yahoo-design.md`

---

## File structure

- **Create:**
  - `src/shortlist/stats.py` — shared `gross_margin_stability()` helper (DRY: one formula for screener + bridge).
  - `src/shortlist/data/bridge.py` — `snapshot_to_metrics()`.
  - `tests/test_stats.py`, `tests/test_bridge.py`, `tests/test_yahoo_source.py`, `tests/test_screen_engine.py`.
- **Modify:**
  - `src/shortlist/models.py` — add `realized_vol`, `max_drawdown` to `StockMetrics`.
  - `src/shortlist/data/models.py` — add `realized_vol`, `max_drawdown` to `Price`.
  - `src/shortlist/data/sources.py` — `YahooSource` + math helpers + registry.
  - `src/shortlist/data/collector.py` — `DEFAULT_PRIORITY` (yahoo first).
  - `src/shortlist/providers/fmp.py` — use the shared stats helper.
  - `src/shortlist/screen.py` — `--engine` flag + `run_harness()`.
  - `config.yaml` — `harness_sources` chain.
  - `.gitignore` — `.cache/`.

---

### Task 1: Shared `gross_margin_stability` helper (DRY)

**Files:**
- Create: `src/shortlist/stats.py`
- Test: `tests/test_stats.py`
- Modify: `src/shortlist/providers/fmp.py:86-96`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stats.py
from shortlist.stats import gross_margin_stability


def test_stable_margins_score_near_one():
    # near-identical margins => very low dispersion => stability ~1.0
    s = gross_margin_stability([0.40, 0.41, 0.40, 0.39])
    assert s is not None and 0.95 < s <= 1.0


def test_volatile_margins_score_lower():
    s = gross_margin_stability([0.10, 0.50, 0.20, 0.45])
    assert s is not None and s < 0.7


def test_fewer_than_three_returns_none():
    assert gross_margin_stability([0.4, 0.4]) is None


def test_zero_mean_returns_none():
    assert gross_margin_stability([0.0, 0.0, 0.0]) is None


def test_never_negative():
    # huge dispersion would push 1 - stdev/mean below 0; clamp to 0.0
    assert gross_margin_stability([0.01, 0.99, 0.02]) == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_stats.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortlist.stats'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/shortlist/stats.py
from __future__ import annotations

from statistics import mean, pstdev
from typing import Optional


def gross_margin_stability(margins: list[float]) -> Optional[float]:
    """0..1 moat proxy: higher = steadier gross margins.

    `max(0, 1 - stdev/mean)` over >=3 yearly gross margins (population stdev, to
    match the screener FMP provider). Returns None with <3 points or zero mean.
    This is the single source of truth for the formula used by BOTH the screener
    provider and the harness bridge — do not reinline it."""
    if len(margins) < 3:
        return None
    avg = mean(margins)
    if not avg:
        return None
    return max(0.0, 1.0 - (pstdev(margins) / avg))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_stats.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Refactor `providers/fmp.py` to use the helper**

In `src/shortlist/providers/fmp.py`, add the import near the other relative imports (top of file, after `from .base import Provider`):

```python
from ..stats import gross_margin_stability
```

Replace the margin-stability block (currently lines ~88-96):

```python
        income = self._get("income-statement", symbol=ticker, period="annual", limit=5)
        if isinstance(income, list) and len(income) >= 3:
            margins = [
                row["grossProfit"] / row["revenue"]
                for row in income
                if row.get("revenue")
            ]
            if len(margins) >= 3:
                avg = mean_(margins)
                m.gross_margin_stability = max(0.0, 1.0 - (stdev_(margins) / avg)) if avg else None
            m.fcf_positive = all(
                (row.get("netIncome") or 0) > 0 for row in income[:2]
            ) or None
```

with:

```python
        income = self._get("income-statement", symbol=ticker, period="annual", limit=5)
        if isinstance(income, list) and len(income) >= 3:
            margins = [
                row["grossProfit"] / row["revenue"]
                for row in income
                if row.get("revenue")
            ]
            m.gross_margin_stability = gross_margin_stability(margins)
            m.fcf_positive = all(
                (row.get("netIncome") or 0) > 0 for row in income[:2]
            ) or None
```

Then delete the now-unused `mean_` and `stdev_` helpers at the bottom of `fmp.py` (the last two `def`s) **only if** a grep shows no other references:

Run: `grep -n "mean_\|stdev_" src/shortlist/providers/fmp.py`
If the only hits are the two definitions, delete both functions.

- [ ] **Step 6: Run the provider tests to confirm no regression**

Run: `uv run pytest tests/test_fmp_provider.py tests/test_stats.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/shortlist/stats.py tests/test_stats.py src/shortlist/providers/fmp.py
git commit -m "refactor: extract shared gross_margin_stability helper"
```

---

### Task 2: New risk fields on `Price` and `StockMetrics`

**Files:**
- Modify: `src/shortlist/data/models.py` (Price, ~line 98-108)
- Modify: `src/shortlist/models.py` (StockMetrics momentum section, ~line 42-44)
- Test: `tests/test_bridge.py` (new file, first test)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_bridge.py
from shortlist.models import StockMetrics
from shortlist.data.models import Price


def test_new_risk_fields_default_none():
    assert StockMetrics(ticker="X").realized_vol is None
    assert StockMetrics(ticker="X").max_drawdown is None
    assert Price().realized_vol is None
    assert Price().max_drawdown is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: FAIL with `TypeError` / `AttributeError` (fields don't exist)

- [ ] **Step 3: Add the fields**

In `src/shortlist/data/models.py`, in the `Price` dataclass, after the `rel_strength_6m` line:

```python
    rel_strength_6m: Optional[float] = None     # 6m return minus benchmark 6m return
    realized_vol: Optional[float] = None        # annualized stdev of daily returns
    max_drawdown: Optional[float] = None        # trailing ~1y peak-to-trough, negative
```

In `src/shortlist/models.py`, in `StockMetrics`, after the `eps_revision` line in the Momentum block:

```python
    eps_revision: Optional[float] = None     # trailing estimate revision trend
    realized_vol: Optional[float] = None     # annualized stdev of daily returns (risk, unscored)
    max_drawdown: Optional[float] = None     # trailing ~1y peak-to-trough, negative (risk, unscored)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/models.py src/shortlist/models.py tests/test_bridge.py
git commit -m "feat: add realized_vol and max_drawdown fields to Price and StockMetrics"
```

---

### Task 3: The bridge — `snapshot_to_metrics()`

**Files:**
- Create: `src/shortlist/data/bridge.py`
- Test: `tests/test_bridge.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_bridge.py`:

```python
from shortlist.data.models import (
    Analyst, Fundamentals, Insider, Profile, Statements, TickerSnapshot,
)
from shortlist.data.bridge import snapshot_to_metrics


def _full_snapshot() -> TickerSnapshot:
    return TickerSnapshot(
        ticker="AAA",
        profile=Profile(name="Triple A", sector="Tech", market_cap=1.0e11),
        fundamentals=Fundamentals(
            pe_ttm=20.0, peg=1.5, fcf_yield=0.05, roe=0.30, roic=0.25,
            gross_margin=0.45, net_margin=0.22, debt_to_equity=0.4,
            interest_coverage=12.0,
        ),
        statements=Statements(
            fiscal_years=[2025, 2024, 2023],
            revenue=[100.0, 90.0, 80.0],
            gross_profit=[45.0, 40.0, 36.0],   # margins ~0.45/0.444/0.45 -> stable
            net_income=[22.0, 20.0, 18.0],
            free_cash_flow=[10.0, 8.0, 7.0],   # most-recent positive
            total_debt=[40.0, 40.0, 40.0],
            total_equity=[100.0, 90.0, 80.0],
        ),
        analyst=Analyst(target_median=120.0, buy=15, hold=3, sell=1),
        insider=Insider(net_value_6m=-500_000.0, sentiment_mspr=0.1),
        price=Price(price=100.0, ma200=80.0, rel_strength_6m=0.06,
                    realized_vol=0.22, max_drawdown=-0.14),
    )


def test_bridge_maps_direct_fields():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.ticker == "AAA"
    assert m.name == "Triple A" and m.sector == "Tech"
    assert m.market_cap == 1.0e11 and m.price == 100.0
    assert m.pe_ttm == 20.0 and m.peg == 1.5 and m.fcf_yield == 0.05
    assert m.roe == 0.30 and m.roic == 0.25
    assert m.gross_margin == 0.45 and m.net_margin == 0.22
    assert m.debt_to_equity == 0.4 and m.interest_coverage == 12.0
    assert m.target_median == 120.0
    assert (m.rating_buy, m.rating_hold, m.rating_sell) == (15, 3, 1)
    assert m.insider_net_6m == -500_000.0 and m.insider_sentiment == 0.1
    assert m.realized_vol == 0.22 and m.max_drawdown == -0.14


def test_bridge_computes_price_vs_200dma():
    m = snapshot_to_metrics(_full_snapshot())
    assert abs(m.price_vs_200dma - (100.0 / 80.0 - 1.0)) < 1e-9
    assert m.rel_strength_6m == 0.06


def test_bridge_derives_stability_and_fcf_positive():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.gross_margin_stability is not None and m.gross_margin_stability > 0.95
    assert m.fcf_positive is True


def test_bridge_fcf_positive_false_when_recent_negative():
    snap = _full_snapshot()
    snap.statements.free_cash_flow = [-5.0, 8.0, 7.0]
    assert snapshot_to_metrics(snap).fcf_positive is False


def test_bridge_parity_gaps_are_none():
    m = snapshot_to_metrics(_full_snapshot())
    assert m.pe_median_5y is None      # harness doesn't fetch ratios history
    assert m.roic_5y_avg is None       # harness doesn't compute 5y roic
    assert m.eps_revision is None      # out of scope (Alpha Vantage)
    assert m.pe_vs_history() is None   # follows from pe_median_5y being None


def test_bridge_empty_snapshot_does_not_raise():
    m = snapshot_to_metrics(TickerSnapshot(ticker="ZZZ"))
    assert m.ticker == "ZZZ"
    assert m.price is None and m.roe is None
    assert m.gross_margin_stability is None and m.fcf_positive is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shortlist.data.bridge'`

- [ ] **Step 3: Write the implementation**

```python
# src/shortlist/data/bridge.py
from __future__ import annotations

from ..models import StockMetrics
from ..stats import gross_margin_stability
from .models import TickerSnapshot


def snapshot_to_metrics(snap: TickerSnapshot) -> StockMetrics:
    """Map a harness TickerSnapshot onto the flat StockMetrics that
    scoring.score() consumes. Pure (no I/O). Absent inputs stay None so the
    scorer's weight-redistribution handles them.

    Two fields are DERIVED here because the harness has the raw material but not
    the field: gross_margin_stability (from Statements) and fcf_positive (most
    recent FCF). Two are accepted None parity gaps the harness does not fetch:
    pe_median_5y and roic_5y_avg. eps_revision is out of scope."""
    m = StockMetrics(ticker=snap.ticker)

    p = snap.profile
    if p:
        m.name = p.name
        m.sector = p.sector
        m.market_cap = p.market_cap

    f = snap.fundamentals
    if f:
        m.pe_ttm = f.pe_ttm
        m.peg = f.peg
        m.fcf_yield = f.fcf_yield
        m.roe = f.roe
        m.roic = f.roic
        m.gross_margin = f.gross_margin
        m.net_margin = f.net_margin
        m.debt_to_equity = f.debt_to_equity
        m.interest_coverage = f.interest_coverage

    pr = snap.price
    if pr:
        m.price = pr.price
        m.price_vs_200dma = pr.price_vs_200dma()
        m.rel_strength_6m = pr.rel_strength_6m
        m.realized_vol = pr.realized_vol
        m.max_drawdown = pr.max_drawdown

    a = snap.analyst
    if a:
        m.target_median = a.target_median
        m.rating_buy = a.buy
        m.rating_hold = a.hold
        m.rating_sell = a.sell

    ins = snap.insider
    if ins:
        m.insider_net_6m = ins.net_value_6m
        m.insider_sentiment = ins.sentiment_mspr

    st = snap.statements
    if st:
        m.gross_margin_stability = gross_margin_stability(st.gross_margins())
        if st.free_cash_flow:
            fcf0 = st.free_cash_flow[0]
            m.fcf_positive = (fcf0 > 0) if fcf0 is not None else None

    # Accepted parity gaps (left None): pe_median_5y, roic_5y_avg, eps_revision.
    return m
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bridge.py -v`
Expected: PASS (all bridge tests)

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/bridge.py tests/test_bridge.py
git commit -m "feat: add TickerSnapshot -> StockMetrics scoring bridge"
```

---

### Task 4: Yahoo math helpers (pure, network-free)

**Files:**
- Modify: `src/shortlist/data/sources.py` (append math helpers near the bottom `helpers` section)
- Test: `tests/test_yahoo_source.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_yahoo_source.py
import math

from shortlist.data.sources import (
    _yh_sma, _yh_ret_over, _yh_annualized_vol, _yh_max_drawdown, _normalize_yahoo,
)


def test_sma():
    assert _yh_sma([1, 2, 3, 4], 2) == 3.5
    assert _yh_sma([1, 2], 5) is None  # not enough points


def test_ret_over():
    assert _yh_ret_over([100, 110], 1) == 0.10
    assert _yh_ret_over([100], 1) is None


def test_annualized_vol_constant_series_is_zero():
    assert _yh_annualized_vol([100.0, 100.0, 100.0, 100.0]) == 0.0


def test_annualized_vol_positive_for_moving_series():
    v = _yh_annualized_vol([100, 101, 99, 102, 98, 103])
    assert v is not None and v > 0


def test_max_drawdown_simple():
    # peak 100 -> trough 80 => -0.20
    md = _yh_max_drawdown([90, 100, 80, 95])
    assert abs(md - (-0.20)) < 1e-9


def test_max_drawdown_monotonic_up_is_zero():
    assert _yh_max_drawdown([100, 110, 120]) == 0.0


def test_normalize_builds_price_with_rel_strength():
    # stock +20% over the 126-day window, SPY +10% => rel strength +10%
    closes = [100.0] * 126 + [120.0]          # len 127, index -127 == 100
    spy = [100.0] * 126 + [110.0]
    snap = _normalize_yahoo("AAA", closes, spy)
    assert snap.price is not None
    assert abs(snap.price.ret_6m - 0.20) < 1e-9
    assert abs(snap.price.rel_strength_6m - 0.10) < 1e-9
    assert snap.price.price == 120.0


def test_normalize_computes_ma200():
    # >=200 points so the 200d SMA is actually computed (the core momentum input).
    closes = [float(i) for i in range(1, 251)]   # 1..250 ascending
    snap = _normalize_yahoo("AAA", closes, [])
    assert snap.price.ma200 == sum(range(51, 251)) / 200   # last 200 = 51..250
    assert snap.price.rel_strength_6m is None              # no SPY series given


def test_normalize_empty_closes_returns_bare_snapshot():
    snap = _normalize_yahoo("AAA", [], [])
    assert snap.ticker == "AAA" and snap.price is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_yahoo_source.py -v`
Expected: FAIL with `ImportError` (helpers not defined)

- [ ] **Step 3: Implement the math helpers + normalizer**

In `src/shortlist/data/sources.py`, add to the `# --- helpers ---` section near the bottom (before `_REGISTRY`). Also ensure `Price` is already imported at the top (it is, in the existing `from .models import (...)`).

```python
# --- Yahoo price math (pure, unit-tested) ---------------------------------

_YH_SIX_MONTHS = 126   # ~trading days in 6 months
_YH_VOL_WINDOW = 252   # ~trading days in 1 year


def _yh_sma(xs: list[float], n: int) -> Optional[float]:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def _yh_ret_over(xs: list[float], n: int) -> Optional[float]:
    return xs[-1] / xs[-1 - n] - 1.0 if len(xs) > n and xs[-1 - n] else None


def _yh_annualized_vol(xs: list[float], window: int = _YH_VOL_WINDOW) -> Optional[float]:
    rets = [xs[i] / xs[i - 1] - 1.0 for i in range(1, len(xs)) if xs[i - 1]][-window:]
    if len(rets) < 2:
        return None
    mu = sum(rets) / len(rets)
    var = sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)
    return (var ** 0.5) * (252 ** 0.5)


def _yh_max_drawdown(xs: list[float], window: int = _YH_VOL_WINDOW) -> Optional[float]:
    s = xs[-window:]
    if len(s) < 2:
        return None
    peak = s[0]
    mdd = 0.0
    for px in s:
        peak = max(peak, px)
        if peak:
            mdd = min(mdd, px / peak - 1.0)
    return mdd


def _normalize_yahoo(ticker: str, closes: list[float], spy_closes: list[float]) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    if not closes:
        return snap
    stock_6m = _yh_ret_over(closes, _YH_SIX_MONTHS)
    spy_6m = _yh_ret_over(spy_closes, _YH_SIX_MONTHS) if spy_closes else None
    rel = stock_6m - spy_6m if (stock_6m is not None and spy_6m is not None) else None
    snap.price = Price(
        price=closes[-1],
        ma200=_yh_sma(closes, 200),
        ret_6m=stock_6m,
        rel_strength_6m=rel,
        realized_vol=_yh_annualized_vol(closes),
        max_drawdown=_yh_max_drawdown(closes),
    )
    return snap
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_yahoo_source.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_yahoo_source.py
git commit -m "feat: add Yahoo price math helpers and normalizer"
```

---

### Task 5: `YahooSource` with day-cache

**Files:**
- Modify: `src/shortlist/data/sources.py` (add class + `_closes_from_chart` parser + registry entry)
- Test: `tests/test_yahoo_source.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_yahoo_source.py`:

```python
import asyncio
import json

from shortlist.data.sources import YahooSource, _closes_from_chart


def _chart_payload(closes):
    return {"chart": {"result": [{
        "indicators": {"adjclose": [{"adjclose": closes}]},
    }]}}


def test_closes_from_chart_filters_nulls():
    raw = _chart_payload([100.0, None, 102.0])
    assert _closes_from_chart(raw) == [100.0, 102.0]


def test_closes_from_chart_handles_garbage():
    assert _closes_from_chart({}) == []
    assert _closes_from_chart({"chart": {"result": []}}) == []


def test_yahoo_source_uses_disk_cache(tmp_path, monkeypatch):
    src = YahooSource(cache_dir=str(tmp_path))
    calls = []

    async def fake_get(symbol):
        calls.append(symbol)
        n = 130
        base = [100.0] * n
        return _chart_payload(base + [120.0] if symbol != "SPY" else base + [110.0])

    monkeypatch.setattr(src, "_get_chart", fake_get)

    res = asyncio.run(src.fetch("AAA"))
    assert res.source == "yahoo"
    assert res.partial.price is not None
    assert res.partial.price.rel_strength_6m is not None
    # AAA + SPY fetched once each
    assert sorted(calls) == ["AAA", "SPY"]

    # second ticker reuses the cached SPY (no second SPY network call)
    asyncio.run(src.fetch("BBB"))
    assert calls.count("SPY") == 1
    asyncio.run(src.aclose())


def test_yahoo_source_error_is_non_fatal(tmp_path, monkeypatch):
    src = YahooSource(cache_dir=str(tmp_path))

    async def boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(src, "_get_chart", boom)
    res = asyncio.run(src.fetch("AAA"))
    assert res.partial.price is None
    assert res.errors and "yahoo" in res.errors[0]
    asyncio.run(src.aclose())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_yahoo_source.py -v -k "chart or yahoo_source"`
Expected: FAIL with `ImportError` / `AttributeError` (`YahooSource`, `_closes_from_chart` undefined)

- [ ] **Step 3: Implement `YahooSource` + parser**

Add near the top of `src/shortlist/data/sources.py`, with the other stdlib imports:

```python
import json
from pathlib import Path
```

Add the parser to the helpers section (near the Yahoo math helpers):

```python
def _closes_from_chart(raw: Any) -> list[float]:
    try:
        result = raw["chart"]["result"][0]
        series = result["indicators"]["adjclose"][0]["adjclose"]
    except (KeyError, IndexError, TypeError):
        return []
    return [c for c in series if isinstance(c, (int, float))]
```

Add the source class after `MockSource` (before the `# --- helpers ---` section):

```python
# --- Yahoo: keyless OHLCV -> we compute momentum/risk ourselves ------------

class YahooSource(Source):
    """Keyless Yahoo chart OHLCV. Computes momentum/risk (rel strength vs SPY,
    realized vol, max drawdown, 200dma) ourselves so the signals are auditable
    and immune to FMP's per-symbol gating. Day-cached on disk; the SPY benchmark
    is fetched once per run and reused across tickers."""

    name = "yahoo"
    BASE = "https://query1.finance.yahoo.com/v8/finance/chart"
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) shortlist/0.1"

    def __init__(self, timeout: float = 15.0, cache_dir: str = ".cache/yahoo"):
        import httpx  # lazy: only needed for live runs
        self._client = httpx.AsyncClient(timeout=timeout, headers={"User-Agent": self.UA})
        self._cache_dir = Path(cache_dir)
        self._spy_closes: Optional[list[float]] = None

    async def aclose(self) -> None:
        await self._client.aclose()

    def _cache_path(self, symbol: str) -> Path:
        return self._cache_dir / f"{symbol.upper()}-{date.today().isoformat()}.json"

    async def _get_chart(self, symbol: str) -> Any:
        """Raw chart payload, day-cached on disk. Override target in tests."""
        cp = self._cache_path(symbol)
        try:
            if cp.exists():
                return json.loads(cp.read_text())
        except Exception:
            pass  # corrupt cache -> refetch
        r = await self._client.get(
            f"{self.BASE}/{symbol}", params={"range": "2y", "interval": "1d"})
        r.raise_for_status()
        raw = r.json()
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cp.write_text(json.dumps(raw))
        except Exception:
            pass  # cache write failure is non-fatal
        return raw

    async def _closes(self, symbol: str) -> list[float]:
        return _closes_from_chart(await self._get_chart(symbol))

    async def _spy(self) -> list[float]:
        if self._spy_closes is None:
            self._spy_closes = await self._closes("SPY")
        return self._spy_closes

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        try:
            closes = await self._closes(ticker)
            spy = await self._spy()
            res.partial = _normalize_yahoo(ticker, closes, spy)
            res.raw = {"close_count": len(closes)}
        except Exception as e:
            res.errors.append(f"yahoo: {redact_secrets(e)}")
            res.partial = TickerSnapshot(ticker=ticker)
        return res
```

Add `"yahoo": YahooSource,` to `_REGISTRY` (first entry, signalling its merge precedence):

```python
_REGISTRY = {
    "yahoo": YahooSource,
    "fmp": FMPSource, "finnhub": FinnhubSource, "edgar": EdgarSource, "mock": MockSource,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_yahoo_source.py -v`
Expected: PASS (all)

- [ ] **Step 5: Add `.cache/` to `.gitignore`**

Append to `.gitignore` (check it isn't already there first with `grep -n cache .gitignore`):

```
.cache/
```

- [ ] **Step 6: Commit**

```bash
git add src/shortlist/data/sources.py tests/test_yahoo_source.py .gitignore
git commit -m "feat: add keyless day-cached YahooSource to the harness"
```

---

### Task 6: Wire Yahoo into the merge priority

**Files:**
- Modify: `src/shortlist/data/collector.py:10` (DEFAULT_PRIORITY)
- Test: `tests/test_harness.py` (append a priority assertion)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_harness.py`:

```python
def test_yahoo_leads_default_priority():
    from shortlist.data.collector import DEFAULT_PRIORITY
    # Yahoo must outrank FMP so its auditable price fields win the price merge.
    assert DEFAULT_PRIORITY.index("yahoo") < DEFAULT_PRIORITY.index("fmp")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_harness.py::test_yahoo_leads_default_priority -v`
Expected: FAIL (`ValueError: 'yahoo' is not in list`)

- [ ] **Step 3: Update `DEFAULT_PRIORITY`**

In `src/shortlist/data/collector.py`, change:

```python
DEFAULT_PRIORITY = ["edgar", "fmp", "finnhub", "mock"]
```

to:

```python
# Yahoo leads for price/momentum (keyless, auditable, gating-immune); EDGAR is
# authoritative for insider; FMP is the fundamentals backbone; Finnhub fills gaps.
DEFAULT_PRIORITY = ["yahoo", "edgar", "fmp", "finnhub", "mock"]
```

- [ ] **Step 4: Run the full harness test file to verify no regression**

Run: `uv run pytest tests/test_harness.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shortlist/data/collector.py tests/test_harness.py
git commit -m "feat: rank Yahoo ahead of FMP in harness merge priority"
```

---

### Task 7: `--engine harness` CLI wiring

**Files:**
- Modify: `src/shortlist/screen.py` (add `run_harness()`, `--engine` arg, branch in `main()`)
- Modify: `config.yaml` (add `harness_sources`)
- Test: `tests/test_screen_engine.py` (new)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_screen_engine.py
import yaml
from pathlib import Path

from shortlist.screen import run_harness, main

CONFIG = yaml.safe_load(
    (Path(__file__).parent.parent / "config.yaml").read_text())


def test_run_harness_scores_mock_snapshots():
    # NOTE: MockSource snapshots have no `statements`, so the bridge's
    # statements-derived fields (gross_margin_stability, fcf_positive) are None
    # on this path by design — that derivation is covered in test_bridge.py.
    # `quality` here comes from `fundamentals` (which mock DOES populate), so it
    # is the right signal that the harness->bridge->score path works end-to-end.
    cards = run_harness(["GEV", "LMT", "GOOGL"], ["mock"], CONFIG)
    assert cards, "expected scored cards from the mock source"
    # sorted descending by composite
    comps = [c.composite for c in cards]
    assert comps == sorted(comps, reverse=True)
    # bridge populated the fundamentals-based metrics the scorer needs
    assert any(c.quality is not None for c in cards)


def test_main_engine_harness_demo_runs(capsys):
    rc = main(["--demo", "--engine", "harness", "--json"])
    assert rc == 0
    out = capsys.readouterr().out
    assert '"composite"' in out


def test_main_default_engine_is_screener_demo(capsys):
    rc = main(["--demo", "--json"])
    assert rc == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_screen_engine.py -v`
Expected: FAIL with `ImportError: cannot import name 'run_harness'`

- [ ] **Step 3: Add `run_harness()` to `screen.py`**

In `src/shortlist/screen.py`, after the existing `run()` function:

```python
def run_harness(tickers: list[str], source_names: list[str], config: dict) -> list[ScoreCard]:
    """Score via the harness stack: collect TickerSnapshots, bridge each to
    StockMetrics, then run the same scorer the screener uses."""
    from .data.bridge import snapshot_to_metrics
    from .data.collector import collect

    snapshots = collect(tickers, source_names)
    cards = [score(snapshot_to_metrics(s), config) for s in snapshots]
    cards.sort(key=lambda c: c.composite, reverse=True)
    return cards
```

- [ ] **Step 4: Add the `--engine` argument**

In `build_arg_parser()`, after the `--provider` argument:

```python
    ap.add_argument("--engine", choices=["screener", "harness"], default="screener",
                    help="screener = synchronous providers (default); "
                         "harness = async sources + TickerSnapshot bridge")
```

- [ ] **Step 5: Branch in `main()`**

Replace the ticker/provider setup block and the `cards = run(...)` call in `main()` (currently ~lines 124-134):

```python
    if args.demo:
        tickers = ["GEV", "LMT", "SCHW", "TMO", "GOOGL"]
        providers = ["mock"]
    else:
        if not args.tickers:
            ap.error("--tickers is required unless --demo")
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
        providers = (args.provider.split(",") if args.provider
                     else config.get("providers", ["fmp"]))

    cards = run(tickers, providers, config)
```

with:

```python
    if not args.demo and not args.tickers:
        ap.error("--tickers is required unless --demo")
    if args.demo:
        tickers = ["GEV", "LMT", "SCHW", "TMO", "GOOGL"]
    else:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]

    if args.engine == "harness":
        if args.demo:
            sources = ["mock"]
        elif args.provider:
            sources = args.provider.split(",")
        else:
            sources = config.get("harness_sources", ["yahoo", "fmp", "finnhub", "edgar"])
        cards = run_harness(tickers, sources, config)
    else:
        if args.demo:
            providers = ["mock"]
        elif args.provider:
            providers = args.provider.split(",")
        else:
            providers = config.get("providers", ["fmp"])
        cards = run(tickers, providers, config)
```

- [ ] **Step 6: Add `harness_sources` to `config.yaml`**

After the `providers:` line in `config.yaml`:

```yaml
# Default source chain for `--engine harness` (Yaml first => wins the price merge).
harness_sources: [yahoo, fmp, finnhub, edgar]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_screen_engine.py -v`
Expected: PASS

- [ ] **Step 8: Run the full suite**

Run: `uv run pytest`
Expected: PASS (all existing + new tests)

- [ ] **Step 9: Smoke-test both engines offline**

Run: `uv run shortlist --demo --engine harness`
Expected: a ranked table prints, no traceback.
Run: `uv run shortlist --demo`
Expected: unchanged screener table.

- [ ] **Step 10: Commit**

```bash
git add src/shortlist/screen.py config.yaml tests/test_screen_engine.py
git commit -m "feat: add --engine harness scoring path"
```

---

### Task 8: Docs — remove A3, update diagrams + HARNESS.md

**Files:**
- Modify: `docs/DATA_SOURCES.md` (remove §A3, adjust §1 table + §4 sequencing)
- Modify: `README.md` (mermaid diagram(s) + any flow text)
- Modify: `HARNESS.md` (document `--engine harness`, YahooSource, bridge)

- [ ] **Step 1: `docs/DATA_SOURCES.md`**
  - Delete the entire **A3** subsection.
  - In the §1 "What we pull today" table, add a **Yahoo Finance chart** row (keyless; both layers via harness; supplies price, 200dma, 6m rel-strength, realized vol, max drawdown).
  - In §2 gap #1, note that 6m rel-strength / vol / drawdown are now computed from Yahoo (gap closed) and that `eps_revision` remains the open momentum gap.
  - In §4 "Recommended sequencing", remove A3 from item 1 and mark it done; renumber. Add the new **TickerSnapshot → StockMetrics bridge** as a delivered architectural item with a pointer to the spec.

- [ ] **Step 2: `README.md`**
  - Update the architecture mermaid diagram to show the harness `Source`s (incl. Yahoo) → `merge_snapshots` → **bridge** → `scoring`, alongside the screener path, with `--engine` selecting between them.
  - Update any user-flow text/diagram to mention `shortlist --engine harness`.

- [ ] **Step 3: `HARNESS.md`**
  - Add a section on `YahooSource` (keyless, day-cached, leads price merge), the `snapshot_to_metrics` bridge, the accepted parity gaps (`pe_median_5y`, `roic_5y_avg`), and the new `realized_vol`/`max_drawdown` fields (populated, unscored).

- [ ] **Step 4: Verify mermaid renders**

Run: `grep -n "mermaid" README.md`
Confirm the fenced blocks are balanced (every ```` ```mermaid ```` has a closing ```` ``` ````).

- [ ] **Step 5: Commit**

```bash
git add docs/DATA_SOURCES.md README.md HARNESS.md
git commit -m "docs: document harness scoring engine + Yahoo source, retire A3 roadmap item"
```

---

## Self-review notes

- **Spec coverage:** bridge (T3), YahooSource + cache (T4/T5), new fields (T2), merge priority (T6), `--engine` (T7), shared stability helper / DRY (T1), docs incl. A3 removal (T8), parity gaps asserted as `None` (T3 test). All spec sections covered.
- **Type consistency:** `snapshot_to_metrics` name used identically in T3/T7; `_get_chart` is the single monkeypatch seam used in T5 tests and called by `_closes`; helper names `_yh_*` consistent across T4 definitions and T4/T5 tests; `gross_margin_stability` signature (list→Optional[float]) consistent T1/T3.
- **No placeholders:** every code step shows complete code; every run step shows the command + expected result.
- **Out of scope confirmed:** no `eps_revision`, no new gate, no `pe_median_5y` fetch, no screener `YahooProvider`.
