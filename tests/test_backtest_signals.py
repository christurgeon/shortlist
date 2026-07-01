from datetime import date, timedelta
from pathlib import Path

import yaml

from shortlist.backtest.prices import PriceHistory
from shortlist.backtest.signals import (
    MomentumSignalSource,
    SnapshotSignalSource,
    XbrlSignalSource,
)
from shortlist.data.sources import snapshot_from_closes
from shortlist.data.bridge import snapshot_to_metrics
from shortlist.data.models import TickerSnapshot, Profile, Fundamentals, Price
from shortlist.data.store import save
from shortlist import scoring

THRESH = {"price_vs_200dma": [-0.10, 0.30], "rel_strength_6m": [-0.15, 0.25],
          "eps_revision": [-0.05, 0.10]}


def _ramp_hist(ticker, n=260, step=1.0, start_px=100.0):
    d0 = date(2020, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    closes = [start_px + i * step for i in range(n)]
    return PriceHistory(ticker, dates, closes)


def test_momentum_observation_matches_production_chain():
    hist = _ramp_hist("AAA")
    spy = _ramp_hist("SPY", step=0.2)
    src = MomentumSignalSource({"AAA": hist}, spy, THRESH, min_history=200)
    T = hist.dates[230]
    obs = src.observe("AAA", T)
    snap = snapshot_from_closes("AAA", hist.closes_through(T), spy.closes_through(T))
    expected = scoring.momentum_score(snapshot_to_metrics(snap), THRESH)
    assert obs is not None
    assert obs.signals["momentum"] == expected      # rides real scoring, no reimpl
    assert obs.as_of == T and obs.ticker == "AAA"


def test_momentum_dropped_when_insufficient_history():
    hist = _ramp_hist("AAA", n=260)
    spy = _ramp_hist("SPY", n=260)
    src = MomentumSignalSource({"AAA": hist}, spy, THRESH, min_history=200)
    early = hist.dates[50]            # only 51 closes <= T, < 200
    assert src.observe("AAA", early) is None


def test_momentum_unknown_ticker_none():
    src = MomentumSignalSource({}, _ramp_hist("SPY"), THRESH)
    assert src.observe("ZZZ", date(2020, 6, 1)) is None


# --- Residual (idiosyncratic) momentum axis on the LIVE-price path (§2) -------

def _wobble_hist(ticker, n=300, beta=1.2, seed=1, start_px=100.0):
    """A history with a real market-beta exposure PLUS idiosyncratic wobble, so the CAPM
    regression has nonzero residual variance (a pure ramp would be degenerate)."""
    d0 = date(2020, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    # Deterministic pseudo-random returns: market + idiosyncratic, distinct per ticker.
    mkt_rets = [0.01 if i % 2 == 0 else -0.006 for i in range(n - 1)]
    closes = [start_px]
    for i, mr in enumerate(mkt_rets):
        idio = ((i * 7 + seed * 13) % 11 - 5) / 1000.0   # deterministic ±0.5% wobble
        closes.append(closes[-1] * (1 + beta * mr + idio))
    return PriceHistory(ticker, dates, closes), dates, mkt_rets


def _mkt_hist(ticker="SPY", n=300, start_px=100.0):
    d0 = date(2020, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    mkt_rets = [0.01 if i % 2 == 0 else -0.006 for i in range(n - 1)]
    closes = [start_px]
    for mr in mkt_rets:
        closes.append(closes[-1] * (1 + mr))
    return PriceHistory(ticker, dates, closes)


def test_momentum_source_emits_residual_momentum_axis():
    # residual_momentum IS price-reconstructable (unlike SUE), so it rides the LIVE-price
    # MomentumSignalSource alongside the production `momentum` sub-score.
    hist, _, _ = _wobble_hist("AAA", n=300)
    spy = _mkt_hist("SPY", n=300)
    t = THRESH | {"residual_momentum": [-1.0, 1.0]}
    src = MomentumSignalSource({"AAA": hist}, spy, t, min_history=200)
    obs = src.observe("AAA", hist.dates[280])
    assert obs is not None
    assert "momentum" in obs.signals
    assert "residual_momentum" in obs.signals
    assert 0.0 <= obs.signals["residual_momentum"] <= 100.0


def test_momentum_production_subscore_unchanged_by_dated_seam():
    # The dated seam only ADDS residual_momentum; the production `momentum` sub-score must be
    # byte-identical to the scalar reconstruction (no look-ahead, no behavior change).
    hist, _, _ = _wobble_hist("AAA", n=300)
    spy = _mkt_hist("SPY", n=300)
    T = hist.dates[280]
    src = MomentumSignalSource({"AAA": hist}, spy, THRESH, min_history=200)
    obs = src.observe("AAA", T)
    scalar = snapshot_from_closes("AAA", hist.closes_through(T), spy.closes_through(T))
    expected = scoring.momentum_score(snapshot_to_metrics(scalar), THRESH)
    assert obs.signals["momentum"] == expected


def test_residual_momentum_axis_is_point_in_time():
    # The beta/residuals must use ONLY data <= as_of. Building the SAME history but with
    # later (post-as_of) closes mutated must NOT change the residual_momentum at as_of.
    hist, _, _ = _wobble_hist("AAA", n=300)
    spy = _mkt_hist("SPY", n=300)
    t = THRESH | {"residual_momentum": [-1.0, 1.0]}
    T = hist.dates[240]
    base = MomentumSignalSource({"AAA": hist}, spy, t, min_history=200).observe("AAA", T)
    # Corrupt every close AFTER T (these are not <= as_of, so must be ignored).
    idx = hist.dates.index(T)
    tampered_closes = list(hist.closes)
    for j in range(idx + 1, len(tampered_closes)):
        tampered_closes[j] = tampered_closes[j] * 3.0 + 1.0
    tampered = PriceHistory("AAA", hist.dates, tampered_closes)
    after = MomentumSignalSource({"AAA": tampered}, spy, t, min_history=200).observe("AAA", T)
    assert base is not None and after is not None
    assert after.signals["residual_momentum"] == base.signals["residual_momentum"]


def test_residual_momentum_axis_dropped_on_flat_window():
    # A flat (constant) stock+market window -> degenerate residuals -> the axis abstains,
    # but the production momentum sub-score still emits (the source still returns an obs).
    n = 300
    d0 = date(2020, 1, 1)
    dates = [d0 + timedelta(days=i) for i in range(n)]
    flat = PriceHistory("AAA", dates, [100.0] * n)          # never moves -> var_m 0
    t = THRESH | {"residual_momentum": [-1.0, 1.0]}
    src = MomentumSignalSource({"AAA": flat}, PriceHistory("SPY", dates, [100.0] * n),
                               t, min_history=200)
    obs = src.observe("AAA", dates[280])
    # A flat series yields rel_strength/price_vs_200dma -> momentum may still score; the
    # residual axis must be ABSENT (None dropped), never an inf/NaN.
    assert obs is None or "residual_momentum" not in obs.signals


def test_snapshot_source_roundtrips_store_and_scores(tmp_path):
    # Build a real snapshot, persist it, then re-score via the snapshot source.
    snap = TickerSnapshot(ticker="AAA", as_of="2026-01-15T00:00:00+00:00")
    snap.profile = Profile(name="A", sector="Tech", market_cap=50e9)
    snap.fundamentals = Fundamentals(roe=0.30, net_margin=0.25,
                                     interest_coverage=10.0, debt_to_equity=0.5)
    snap.price = Price(price=120.0, ma200=100.0, rel_strength_6m=0.1)
    save(snap, str(tmp_path))

    config = {
        "thresholds": THRESH | {"roe": [0.10, 0.35], "net_margin": [0.0, 0.30],
                                "interest_coverage": [2.0, 15.0], "debt_to_equity": [3.0, 0.0],
                                "gross_margin": [0.20, 0.70], "gross_margin_stability": [0.5, 1.0],
                                "roic": [0.05, 0.30], "revenue_cagr": [0.0, 0.20],
                                "fcf_cagr": [0.0, 0.20], "eps_cagr": [0.0, 0.20],
                                "revenue_growth_persistence": [0.5, 1.0],
                                "upside_to_target": [0.0, 0.40], "fcf_yield": [0.02, 0.08],
                                "pe_vs_history": [-0.20, 0.30], "peg": [3.0, 0.5],
                                "insider_sentiment": [-0.30, 0.30],
                                "insider_net_ratio": [-0.0005, 0.0005]},
        "weights": {"quality": 0.2, "moat": 0.2, "growth": 0.15,
                    "value": 0.22, "momentum": 0.08, "insider": 0.15},
        "gates": {"min_market_cap": 2e9, "max_debt_to_equity": 5.0,
                  "min_insider_sentiment": -0.60},
    }
    src = SnapshotSignalSource(str(tmp_path), config)
    obs = src.observe("AAA", date(2026, 1, 15))
    assert obs is not None
    assert "composite" in obs.signals
    assert obs.signals["composite"] > 0              # quality + momentum present
    assert obs.ticker == "AAA"


def test_snapshot_source_missing_day_none(tmp_path):
    src = SnapshotSignalSource(str(tmp_path), {"thresholds": {}, "weights": {}, "gates": {}})
    assert src.observe("ZZZ", date(2026, 1, 15)) is None


def test_snapshot_source_emits_sue_axis_when_earnings_accumulated(tmp_path):
    # SUE (§1) rides ONLY the snapshot-replay path: it emits a standalone `sue` axis when
    # an accumulated snapshot carries the earnings inputs (no-op otherwise).
    from shortlist.data.models import Earnings
    snap = TickerSnapshot(ticker="AAA", as_of="2026-01-15T00:00:00+00:00")
    snap.profile = Profile(name="A", sector="Tech", market_cap=50e9)
    snap.price = Price(price=120.0, ma200=100.0, rel_strength_6m=0.1)
    # A fresh +10% beat over 4 quarters -> positive SUE -> scores above the band midpoint.
    snap.earnings = Earnings(as_of="2026-01-15", recent_surprise_pcts=[10.0, 0.0, 5.0, -5.0],
                             quarters=4, last_surprise_pct=10.0, last_report_date="2026-01-15")
    save(snap, str(tmp_path))
    config = {"thresholds": THRESH | {"sue": [-2.0, 2.0],
                                      "insider_sentiment": [-0.30, 0.30],
                                      "insider_net_ratio": [-0.0005, 0.0005]},
              "weights": {"quality": 0.2, "moat": 0.2, "growth": 0.15,
                          "value": 0.22, "momentum": 0.08, "insider": 0.15},
              "gates": {"min_market_cap": 2e9, "max_debt_to_equity": 5.0,
                        "min_insider_sentiment": -0.60}}
    obs = SnapshotSignalSource(str(tmp_path), config).observe("AAA", date(2026, 1, 15))
    assert obs is not None
    assert "sue" in obs.signals and obs.signals["sue"] > 50.0   # a fresh beat tilts up


# --- XBRL source: EV/EBIT + per-leg value attribution axes (spec §11) ---------

_XBRL_THRESH = yaml.safe_load(
    (Path(__file__).parents[1] / "config.yaml").read_text()
)["thresholds"]


def _row(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}


def _inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}


def _annual(concept, rows, unit="USD"):
    return {concept: {"units": {unit: rows}}}


def _facts_all_value_legs():
    """A name with everything the four EV/EBIT axes need: revenue (so the panel is
    non-empty), diluted EPS + shares + price history (pe_vs_history + market cap),
    FCF (fcf_yield), operating income + debt + cash (EBIT, net_debt -> ebit_ev_yield)."""
    g = {}
    g.update(_annual("Revenues", [
        _row("2020-01-01", "2020-12-31", 1000, "2021-02-01"),
        _row("2021-01-01", "2021-12-31", 1100, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 1300, "2023-02-01")]))
    g.update(_annual("NetIncomeLoss", [
        _row("2021-01-01", "2021-12-31", 120, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 160, "2023-02-01")]))
    g.update(_annual("OperatingIncomeLoss", [
        _row("2022-01-01", "2022-12-31", 250, "2023-02-01")]))   # EBIT = 250
    g.update(_annual("NetCashProvidedByUsedInOperatingActivities", [
        _row("2022-01-01", "2022-12-31", 240, "2023-02-01")]))
    g.update(_annual("PaymentsToAcquirePropertyPlantAndEquipment", [
        _row("2022-01-01", "2022-12-31", 40, "2023-02-01")]))    # FCF = 200
    g.update(_annual("EarningsPerShareDiluted", [
        _row("2020-01-01", "2020-12-31", 2.2, "2021-02-01"),
        _row("2021-01-01", "2021-12-31", 2.6, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 3.2, "2023-02-01")], unit="USD/shares"))
    # total_debt = LongTermDebt + DebtCurrent, cash -> net_debt at the latest end.
    g.update(_annual("LongTermDebt", [_inst("2022-12-31", 150, "2023-02-01")]))
    g.update(_annual("DebtCurrent", [_inst("2022-12-31", 50, "2023-02-01")]))   # debt = 200
    g.update(_annual("CashAndCashEquivalentsAtCarryingValue",
                     [_inst("2022-12-31", 120, "2023-02-01")]))   # net_debt = 80
    dei = _annual("EntityCommonStockSharesOutstanding",
                  [_inst("2022-12-31", 10, "2023-02-01")], unit="shares")
    return {"facts": {"us-gaap": g, "dei": dei}}


def _price_history(ticker="TST"):
    dates, closes = [], []
    for y in (2020, 2021, 2022, 2023):
        for mo in range(1, 13):
            dates.append(date(y, mo, 28))
            closes.append(40.0 + (y - 2020) * 10 + mo)
    # No split in this fixture -> nominal (unadjusted) == adjusted close.
    return PriceHistory(ticker, dates, closes, nominal_closes=list(closes))


_PRICE_AXIS_BANDS = {
    "pct_to_52w_high": [0.70, 1.00],
    "max_daily_return": [0.15, 0.02],
    "vol_scaled_momentum": [0.0, 2.0],
    "residual_momentum": [-1.0, 1.0],
}


def test_momentum_source_emits_price_refinement_axes():
    hist, _, _ = _wobble_hist("AAA", n=300)
    spy = _mkt_hist("SPY", n=300)
    t = THRESH | _PRICE_AXIS_BANDS
    src = MomentumSignalSource({"AAA": hist}, spy, t, min_history=200)
    obs = src.observe("AAA", hist.dates[290])
    assert obs is not None
    for axis in ("pct_to_52w_high", "max_daily_return", "vol_scaled_momentum",
                 "price_vs_200dma", "rel_strength_6m"):
        assert axis in obs.signals
        assert 0.0 <= obs.signals[axis] <= 100.0


def test_momentum_subscore_unchanged_by_price_refinement_axes():
    # Use _wobble_hist (real stock-vs-SPY divergence), NOT _ramp_hist: with a flat ramp
    # momentum_score coincidentally equals rel_strength_6m_score (~37.5), so an emission-loop
    # bug that overwrote sig["momentum"] with an axis value would slip past the equality
    # assertion. Wobble breaks that tie (asserted below) so the guard is real.
    hist, _, _ = _wobble_hist("AAA", n=300)
    spy = _mkt_hist("SPY", n=300)
    T = hist.dates[290]
    with_axes = MomentumSignalSource({"AAA": hist}, spy, THRESH | _PRICE_AXIS_BANDS,
                                     min_history=200).observe("AAA", T)
    plain = MomentumSignalSource({"AAA": hist}, spy, THRESH,
                                 min_history=200).observe("AAA", T)
    assert with_axes.signals["momentum"] == plain.signals["momentum"]
    # Non-degeneracy: momentum must NOT coincide with an emitted axis, else an overwrite
    # would slip past the equality above (the bug _ramp_hist hid).
    assert with_axes.signals["momentum"] != with_axes.signals["rel_strength_6m"]


def test_price_refinement_axes_are_point_in_time():
    hist, _, _ = _wobble_hist("AAA", n=320)
    spy = _mkt_hist("SPY", n=320)
    t = THRESH | _PRICE_AXIS_BANDS
    T = hist.dates[290]
    base = MomentumSignalSource({"AAA": hist}, spy, t, min_history=200).observe("AAA", T)
    idx = hist.dates.index(T)
    tampered_closes = list(hist.closes)
    for j in range(idx + 1, len(tampered_closes)):
        tampered_closes[j] = tampered_closes[j] * 3.0 + 1.0
    tampered = PriceHistory("AAA", hist.dates, tampered_closes)
    after = MomentumSignalSource({"AAA": tampered}, spy, t, min_history=200).observe("AAA", T)
    for axis in ("pct_to_52w_high", "max_daily_return", "vol_scaled_momentum"):
        assert after.signals[axis] == base.signals[axis]


def test_xbrl_source_emits_ev_ebit_axes():
    # For a name with EBIT + market cap + net_debt + fcf_yield + a PE history,
    # all four absolute-valuation / per-leg attribution axes appear in signals.
    src = XbrlSignalSource(
        {"TEST": _facts_all_value_legs()},
        {"TEST": _price_history("TEST")},
        _XBRL_THRESH,
    )
    obs = src.observe("TEST", date(2023, 6, 1))
    assert obs is not None
    assert "ebit_ev_yield" in obs.signals
    assert "value_fcf_yield" in obs.signals
    assert "value_pe_vs_history" in obs.signals
    assert "value_plus_evebit" in obs.signals
    assert all(0.0 <= v <= 100.0 for v in obs.signals.values())


def test_observe_uses_nominal_close_for_market_cap(monkeypatch):
    seen = {}

    def fake_panel_to_metrics(panel, *, ticker, sic, price, price_at):
        seen["price"] = price
        seen["price_at"] = price_at(date(2020, 6, 30))
        from shortlist.scoring import StockMetrics
        return StockMetrics(ticker=ticker)

    def fake_extract_panel(cf, as_of):
        class _P:  # truthy .revenue so observe() doesn't early-return
            revenue = {"2020-12-31": 1.0}
        return _P()

    import shortlist.backtest.signals as sig_mod
    monkeypatch.setattr(sig_mod, "panel_to_metrics", fake_panel_to_metrics)
    monkeypatch.setattr(sig_mod, "extract_panel", fake_extract_panel)

    hist = PriceHistory("X", [date(2020, 6, 30), date(2021, 1, 4)],
                        closes=[45.0, 50.0],          # adjusted (post-split)
                        nominal_closes=[90.0, 100.0])  # unadjusted (what a live observer saw)
    src = XbrlSignalSource(facts={"X": {"cik": "1"}}, histories={"X": hist}, thresholds={})
    try:
        src.observe("X", date(2021, 1, 5))
    except KeyError:
        pass  # empty thresholds{} trips the axis-scoring loop AFTER panel_to_metrics
              # already captured price/price_at into `seen` -- that's all this test checks
    assert seen["price"] == 100.0          # nominal at as_of, NOT the adjusted 50.0
    assert seen["price_at"] == 90.0        # nominal at the historical PE-year date
