import asyncio
import pandas as pd
import pytest

from shortlist.data.sources import EdgarSource


class _FakeStatement:
    def __init__(self, df): self._df = df
    def to_dataframe(self): return self._df


class _FakeFinancials:
    def __init__(self, inc, cf): self._inc, self._cf = inc, cf
    def income_statement(self): return _FakeStatement(self._inc)
    def cashflow_statement(self): return _FakeStatement(self._cf)
    def get_shares_outstanding_diluted(self): return 15_004_697_000.0


def _inc():
    return pd.DataFrame([
        {"standard_concept": "Revenue", "label": "r", "2025-09-27 (FY)": 416_161_000_000.0, "2024-09-28 (FY)": 391_035_000_000.0},
        {"standard_concept": "NetIncomeLoss", "label": "ni", "2025-09-27 (FY)": 112_010_000_000.0, "2024-09-28 (FY)": 93_736_000_000.0},
        {"standard_concept": float("nan"), "label": "Diluted (in dollars per share)", "2025-09-27 (FY)": 7.46, "2024-09-28 (FY)": 6.08},
    ])


def _cf():
    return pd.DataFrame([
        {"standard_concept": "NetCashFromOperatingActivities", "label": "ocf", "2025-09-27 (FY)": 111_482_000_000.0, "2024-09-28 (FY)": 118_254_000_000.0},
        {"standard_concept": "CapitalExpenses", "label": "capex", "2025-09-27 (FY)": -12_715_000_000.0, "2024-09-28 (FY)": -9_447_000_000.0},
    ])


def test_build_financials_snapshot_fills_statements():
    src = EdgarSource.__new__(EdgarSource)        # bypass __init__ (no SEC identity / network)
    src.name = "edgar"
    snap = src._build_financials_snapshot("AAPL", _FakeFinancials(_inc(), _cf()))
    assert snap.statements.revenue == [416_161_000_000.0, 391_035_000_000.0]
    assert snap.statements.free_cash_flow == [pytest.approx(98_767_000_000.0), pytest.approx(108_807_000_000.0)]
    assert snap.statements.diluted_eps == [7.46, 6.08]
    assert snap.statements.fiscal_period_end == ["2025-09-27", "2024-09-28"]
    assert snap.statements.fiscal_years == [2025, 2024]


def test_build_financials_snapshot_empty_on_no_data():
    src = EdgarSource.__new__(EdgarSource); src.name = "edgar"
    snap = src._build_financials_snapshot("ZZZ", _FakeFinancials(pd.DataFrame(), pd.DataFrame()))
    assert snap.statements is None       # 20-F / no XBRL -> no statements, no crash


def test_financials_failure_does_not_drop_insider(monkeypatch):
    """A financials exception must be caught, logged, and leave insider intact."""
    src = EdgarSource.__new__(EdgarSource); src.name = "edgar"; src.lookback_days = 183

    from shortlist.data.models import SourceResult, TickerSnapshot, Insider
    def fake_insider(self, ticker):
        r = SourceResult(source="edgar")
        r.partial = TickerSnapshot(ticker=ticker, insider=Insider(buy_count=3, sell_count=0))
        return r
    monkeypatch.setattr(EdgarSource, "_fetch_insider", fake_insider, raising=False)
    monkeypatch.setattr(EdgarSource, "_fetch_financials_object",
                        lambda self, t: (_ for _ in ()).throw(RuntimeError("SEC 503")), raising=False)

    res = src._fetch_sync("AAPL")
    assert res.partial.insider.buy_count == 3            # insider survived
    assert any("edgar-financials" in e for e in res.errors)
    assert res.partial.statements is None
