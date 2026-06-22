import asyncio
import os
import pandas as pd
import pytest

from shortlist.data.sources import EdgarSource


class _FakeStatement:
    def __init__(self, df): self._df = df
    def to_dataframe(self): return self._df


class _FakeFinancials:
    def __init__(self, inc, cf, bal=None):
        self._inc, self._cf = inc, cf
        self._bal = bal if bal is not None else pd.DataFrame()
    def income_statement(self): return _FakeStatement(self._inc)
    def cashflow_statement(self): return _FakeStatement(self._cf)
    def balance_sheet(self): return _FakeStatement(self._bal)
    def get_shares_outstanding_diluted(self): return 15_004_697_000.0


def _inc():
    return pd.DataFrame([
        {"standard_concept": "Revenue", "label": "r", "2025-09-27 (FY)": 416_161_000_000.0, "2024-09-28 (FY)": 391_035_000_000.0},
        {"standard_concept": "NetIncome", "label": "ni", "2025-09-27 (FY)": 112_010_000_000.0, "2024-09-28 (FY)": 93_736_000_000.0},
        {"standard_concept": float("nan"), "label": "Diluted (in dollars per share)", "2025-09-27 (FY)": 7.46, "2024-09-28 (FY)": 6.08},
    ])


def _cf():
    return pd.DataFrame([
        {"standard_concept": "NetCashFromOperatingActivities", "label": "ocf", "2025-09-27 (FY)": 111_482_000_000.0, "2024-09-28 (FY)": 118_254_000_000.0},
        {"standard_concept": "CapitalExpenses", "label": "capex", "2025-09-27 (FY)": -12_715_000_000.0, "2024-09-28 (FY)": -9_447_000_000.0},
        {"standard_concept": "DepreciationExpense", "label": "d&a", "2025-09-27 (FY)": 12_000_000_000.0, "2024-09-28 (FY)": 11_000_000_000.0},
    ])


def _bal():
    # Balance sheet: INSTANT date columns (no "(FY)" suffix), edgartools taxonomy.
    return pd.DataFrame([
        {"standard_concept": "CashAndMarketableSecurities", "label": "cash", "2025-09-27": 30_000_000_000.0, "2024-09-28": 29_000_000_000.0},
        {"standard_concept": "LongTermDebt", "label": "term debt", "2025-09-27": 80_000_000_000.0, "2024-09-28": 85_000_000_000.0},
        {"standard_concept": "CurrentPortionOfLongTermDebt", "label": "term debt cur", "2025-09-27": 10_000_000_000.0, "2024-09-28": 11_000_000_000.0},
    ])


def test_build_financials_snapshot_fills_statements():
    src = EdgarSource.__new__(EdgarSource)        # bypass __init__ (no SEC identity / network)
    src.name = "edgar"
    snap = src._build_financials_snapshot("AAPL", _FakeFinancials(_inc(), _cf(), _bal()))
    assert snap.statements.revenue == [416_161_000_000.0, 391_035_000_000.0]
    assert snap.statements.free_cash_flow == [pytest.approx(98_767_000_000.0), pytest.approx(108_807_000_000.0)]
    assert snap.statements.diluted_eps == [7.46, 6.08]
    assert snap.statements.fiscal_period_end == ["2025-09-27", "2024-09-28"]
    assert snap.statements.fiscal_years == [2025, 2024]
    # Leverage fields (§2.7): D&A from cash-flow, debt summed, cash from balance.
    assert snap.statements.dep_amort == [12_000_000_000.0, 11_000_000_000.0]
    assert snap.statements.total_debt == [90_000_000_000.0, 96_000_000_000.0]
    assert snap.statements.cash_and_equivalents == [30_000_000_000.0, 29_000_000_000.0]


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


@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_edgar_financials_10k_filer():
    res = asyncio.run(EdgarSource().fetch("LMT"))     # SEC_IDENTITY from env
    assert res.partial.statements is not None
    assert res.partial.statements.free_cash_flow
    assert res.partial.statements.diluted_eps


@pytest.mark.skipif(not os.environ.get("RUN_LIVE_EDGAR"), reason="live SEC call; set RUN_LIVE_EDGAR=1")
def test_live_edgar_foreign_or_nofinancials_degrades_cleanly():
    # A 20-F foreign issuer (ASML) should not crash; statements may be None.
    res = asyncio.run(EdgarSource().fetch("ASML"))
    assert res.partial is not None                    # no exception escaped
    # Either parsed statements or a logged financials error — never a crash.
    assert (
        res.partial.statements is not None
        or any("edgar-financials" in e for e in res.errors)
        or res.partial.statements is None
    )
