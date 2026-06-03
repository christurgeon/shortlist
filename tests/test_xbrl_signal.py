# tests/test_xbrl_signal.py
from datetime import date
from pathlib import Path
import yaml
from shortlist.backtest.signals import XbrlSignalSource
from shortlist.backtest.prices import PriceHistory

CONFIG = yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())
THRESH = CONFIG["thresholds"]

def _row(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}

def _inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}

def _annual(concept, rows, unit="USD"):
    return {concept: {"units": {unit: rows}}}

def _facts_for():
    gaap = {}
    gaap.update(_annual("Revenues", [
        _row("2020-01-01", "2020-12-31", 1000, "2021-02-01"),
        _row("2021-01-01", "2021-12-31", 1100, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 1300, "2023-02-01")]))
    gaap.update(_annual("NetIncomeLoss", [
        _row("2020-01-01", "2020-12-31", 100, "2021-02-01"),
        _row("2021-01-01", "2021-12-31", 120, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 160, "2023-02-01")]))
    gaap.update(_annual("GrossProfit", [
        _row("2020-01-01", "2020-12-31", 500, "2021-02-01"),
        _row("2021-01-01", "2021-12-31", 560, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 700, "2023-02-01")]))
    gaap.update(_annual("NetCashProvidedByUsedInOperatingActivities", [
        _row("2022-01-01", "2022-12-31", 240, "2023-02-01")]))
    gaap.update(_annual("PaymentsToAcquirePropertyPlantAndEquipment", [
        _row("2022-01-01", "2022-12-31", 40, "2023-02-01")]))
    gaap.update(_annual("EarningsPerShareDiluted", [
        _row("2021-01-01", "2021-12-31", 2.6, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 3.2, "2023-02-01")], unit="USD/shares"))
    gaap.update(_annual("StockholdersEquity", [_inst("2022-12-31", 600, "2023-02-01")]))
    return {"facts": {"us-gaap": gaap,
                      "dei": _annual("EntityCommonStockSharesOutstanding",
                                     [_inst("2022-12-31", 10, "2023-02-01")],
                                     unit="shares")}}

def _price_history():
    dates, closes = [], []
    for y in (2020, 2021, 2022, 2023):
        for mo in range(1, 13):
            dates.append(date(y, mo, 28))
            closes.append(40.0 + (y - 2020) * 10 + mo)
    return PriceHistory("TST", dates, closes)

def test_xbrl_signal_emits_fundamental_subscores():
    src = XbrlSignalSource({"TST": _facts_for()}, {"TST": _price_history()}, THRESH)
    obs = src.observe("TST", date(2023, 6, 1))
    assert obs is not None and obs.as_of == date(2023, 6, 1)
    for axis in ("quality", "moat", "growth", "value"):
        assert axis in obs.signals
    assert "momentum" not in obs.signals and "insider" not in obs.signals
    assert all(0.0 <= v <= 100.0 for v in obs.signals.values())

def test_xbrl_signal_returns_none_for_unknown_ticker():
    assert XbrlSignalSource({}, {}, THRESH).observe("NOPE", date(2023, 6, 1)) is None

def test_xbrl_signal_emits_fundamentals_without_price_history():
    # Company filed 10-Ks before the price series starts: value legs drop (no price),
    # but quality/moat/growth still score.
    src = XbrlSignalSource({"TST": _facts_for()}, {}, THRESH)   # no histories
    obs = src.observe("TST", date(2023, 6, 1))
    assert obs is not None
    for axis in ("quality", "moat", "growth"):
        assert axis in obs.signals
    assert "value" not in obs.signals      # no price -> fcf_yield + pe legs are None

def test_xbrl_signal_returns_none_for_facts_without_us_gaap_revenue():
    # IFRS 20-F filer (data under ifrs-full) -> empty us-gaap panel -> dropped, not zeroed
    src = XbrlSignalSource({"IFRS": {"facts": {"ifrs-full": {}}}}, {}, THRESH)
    assert src.observe("IFRS", date(2023, 6, 1)) is None
