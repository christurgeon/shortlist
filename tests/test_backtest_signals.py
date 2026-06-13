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
    return PriceHistory(ticker, dates, closes)


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
