"""Point-in-time tests for filings.filing_text_change (the "Lazy Prices" §4
PiT accessor). Injects a fake `edgar` module so no SEC requests are made and the
LOOK-AHEAD guard can be asserted directly: the comparison must never use a filing
dated after as_of."""
import sys
import types

import pytest

from shortlist.research import filings


class _FakeObj:
    def __init__(self, risk="", mda=""):
        self.risk_factors = risk
        self.management_discussion = mda


class _FakeFiling:
    def __init__(self, form, date, accession, risk="", mda=""):
        self.form = form
        self.filing_date = date
        self.accession_no = accession
        self._obj = _FakeObj(risk, mda)

    def obj(self):
        return self._obj


class _FakeFilings:
    """Mimics edgartools' get_filings(form=...) -> iterable of filings."""
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)


class _FakeCompany:
    rows_by_form: dict = {}

    def __init__(self, ticker):
        self.ticker = ticker

    def get_filings(self, form=None):
        return _FakeFilings(list(self.rows_by_form.get(form, [])))


@pytest.fixture
def fake_edgar(monkeypatch):
    """Install a fake `edgar` module exposing Company + set_identity."""
    mod = types.ModuleType("edgar")
    mod.Company = _FakeCompany
    mod.set_identity = lambda *_a, **_k: None
    monkeypatch.setitem(sys.modules, "edgar", mod)
    monkeypatch.setenv("SEC_IDENTITY", "tester@example.com")
    yield


def _set_rows(form, rows):
    _FakeCompany.rows_by_form = {form: rows}


def test_compares_current_vs_immediately_prior(fake_edgar):
    rows = [
        _FakeFiling("10-K", "2025-02-01", "acc-2025", risk="cyber risk evolving",
                    mda="growth strong"),
        _FakeFiling("10-K", "2024-02-01", "acc-2024", risk="competition risk steady",
                    mda="growth steady"),
        _FakeFiling("10-K", "2023-02-01", "acc-2023", risk="anything older",
                    mda="ancient"),
    ]
    _set_rows("10-K", rows)
    out = filings.filing_text_change("X", form="10-K", as_of=None)
    assert out is not None
    # Compared the two MOST-RECENT filings, not the older one.
    assert out["current_accession"] == "acc-2025"
    assert out["prior_accession"] == "acc-2024"
    assert 0.0 <= out["similarity"] <= 1.0


def test_point_in_time_never_uses_future_filing(fake_edgar):
    # A 2026 filing exists but as_of is mid-2025: it MUST be excluded, so the
    # comparison is the 2025 vs 2024 pair (no look-ahead).
    rows = [
        _FakeFiling("10-K", "2026-02-01", "acc-2026-FUTURE", risk="future text",
                    mda="future"),
        _FakeFiling("10-K", "2025-02-01", "acc-2025", risk="cyber risk evolving",
                    mda="growth strong"),
        _FakeFiling("10-K", "2024-02-01", "acc-2024", risk="competition risk steady",
                    mda="growth steady"),
    ]
    _set_rows("10-K", rows)
    out = filings.filing_text_change("X", form="10-K", as_of="2025-06-30")
    assert out is not None
    assert out["current_accession"] == "acc-2025"     # NOT acc-2026-FUTURE
    assert out["prior_accession"] == "acc-2024"
    assert out["current_date"] <= "2025-06-30"
    assert out["prior_date"] <= "2025-06-30"


def test_returns_none_when_fewer_than_two_at_as_of(fake_edgar):
    rows = [
        _FakeFiling("10-K", "2025-02-01", "acc-2025", risk="x", mda="y"),
        _FakeFiling("10-K", "2024-02-01", "acc-2024", risk="z", mda="w"),
    ]
    _set_rows("10-K", rows)
    # as_of before the prior filing -> only one (or zero) available -> None.
    assert filings.filing_text_change("X", form="10-K", as_of="2024-06-30") is None


def test_drops_amendments_and_other_forms(fake_edgar):
    rows = [
        _FakeFiling("10-K/A", "2025-03-01", "acc-amend", risk="amended", mda="a"),
        _FakeFiling("10-K", "2025-02-01", "acc-2025", risk="cyber risk", mda="m"),
        _FakeFiling("10-K", "2024-02-01", "acc-2024", risk="comp risk", mda="m2"),
    ]
    _set_rows("10-K", rows)
    out = filings.filing_text_change("X", form="10-K", as_of=None)
    assert out is not None
    assert out["current_accession"] == "acc-2025"     # /A amendment skipped


def test_identical_text_yields_high_similarity(fake_edgar):
    same_risk = "We face supply chain risk and competition risk in our markets."
    same_mda = "Revenue grew on strong demand across all segments."
    rows = [
        _FakeFiling("10-K", "2025-02-01", "acc-2025", risk=same_risk, mda=same_mda),
        _FakeFiling("10-K", "2024-02-01", "acc-2024", risk=same_risk, mda=same_mda),
    ]
    _set_rows("10-K", rows)
    out = filings.filing_text_change("X", form="10-K", as_of=None)
    assert out is not None and out["similarity"] > 0.99


def test_returns_none_on_empty_text(fake_edgar):
    rows = [
        _FakeFiling("10-K", "2025-02-01", "acc-2025", risk="", mda=""),
        _FakeFiling("10-K", "2024-02-01", "acc-2024", risk="", mda=""),
    ]
    _set_rows("10-K", rows)
    assert filings.filing_text_change("X", form="10-K", as_of=None) is None
