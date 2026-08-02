"""Task 1: pure companyconcept aggregator (`diluted_shares_from_concept`).
Task 2: the mockable network seam + fallback wiring on EdgarSource, isolated
from the rest of Statements construction (docs/PLAN_EDGAR_ROOT_CAUSE_B.md)."""
from __future__ import annotations

import pandas as pd

from shortlist.data.sources import EdgarSource
from shortlist.providers._edgar_facts import diluted_shares_from_concept

SPINE = ["2025-12-31", "2024-12-31", "2023-12-31"]


def _row(end, val, *, start=None, form="10-K", filed="2026-02-17"):
    """A single companyconcept 'shares' unit row. Default start is one 10-K
    annual duration (365d) before `end`."""
    if start is None:
        y, m, d = (int(x) for x in end.split("-"))
        start = f"{y - 1}-{m:02d}-{d:02d}"
    return {"start": start, "end": end, "val": val, "form": form,
            "fy": y_from(end), "fp": "FY", "filed": filed, "accn": "0001-25-000001"}


def y_from(end: str) -> int:
    return int(end[:4])


def _payload(rows: list[dict]) -> dict:
    return {"units": {"shares": rows}}


# --- happy path -------------------------------------------------------------

def test_happy_path_three_annual_10k_entries_match_spine_order():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == [
        643_000_000.0, 655_000_000.0, 668_000_000.0]


# --- restatement dedup -------------------------------------------------------

def test_restatement_dedup_prefers_later_filed():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0, filed="2025-02-15"),      # original filing
        _row("2024-12-31", 654_500_000.0, filed="2026-02-17"),      # restated, later filed
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 654_500_000.0, 668_000_000.0]


def test_restatement_dedup_ties_keep_the_last_seen_via_gte():
    # Same `filed` twice for the same `end`: the >= comparison means the later
    # row in iteration order wins (not the first).
    payload = _payload([
        _row("2025-12-31", 111.0, filed="2026-01-01"),
        _row("2025-12-31", 222.0, filed="2026-01-01"),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out[0] == 222.0


# --- duration guard -----------------------------------------------------------

def test_quarterly_entry_is_ignored_even_if_filed_later():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        # A quarterly fact sharing the same `end` as a spine year, filed AFTER
        # the annual row, with a deliberately wrong value — must not win.
        _row("2024-12-31", 999_999_999.0, start="2024-10-01", filed="2027-01-01"),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 655_000_000.0, 668_000_000.0]


def test_quarterly_only_year_leaves_that_year_uncovered_so_result_abstains():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 999_999_999.0, start="2024-10-01"),  # quarterly only
        _row("2023-12-31", 668_000_000.0),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


# --- partial coverage abstains -------------------------------------------------

def test_partial_coverage_two_of_three_years_abstains():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        # 2023-12-31 missing entirely
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


def test_spine_year_entirely_absent_from_payload_abstains():
    # Payload only ever mentions fiscal years outside the requested spine.
    payload = _payload([
        _row("2022-12-31", 700_000_000.0),
        _row("2021-12-31", 710_000_000.0),
        _row("2020-12-31", 720_000_000.0),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


# --- form filtering -----------------------------------------------------------

def test_non_10k_forms_are_ignored():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 111.0, form="8-K", filed="2026-03-01"),  # would win dedup if counted
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 655_000_000.0, 668_000_000.0]


def test_only_non_10k_forms_present_abstains():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0, form="10-K/A"),
        _row("2024-12-31", 655_000_000.0, form="8-K"),
        _row("2023-12-31", 668_000_000.0, form="10-K"),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


# --- malformed / empty input never raises --------------------------------------

def test_empty_payload_abstains():
    assert diluted_shares_from_concept({}, SPINE) == []


def test_none_payload_abstains():
    assert diluted_shares_from_concept(None, SPINE) == []


def test_missing_units_key_abstains():
    assert diluted_shares_from_concept({"other": 1}, SPINE) == []


def test_units_present_but_shares_missing_abstains():
    assert diluted_shares_from_concept({"units": {"usd": []}}, SPINE) == []


def test_units_shares_wrong_type_abstains():
    assert diluted_shares_from_concept({"units": {"shares": "not-a-list"}}, SPINE) == []


def test_malformed_row_entries_are_skipped_not_raised():
    payload = _payload([
        "not-a-dict",
        {"form": "10-K"},                      # missing start/end/val
        {"form": "10-K", "start": "bad", "end": "2025-12-31", "val": 1.0, "filed": "x"},
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 655_000_000.0, 668_000_000.0]


def test_empty_fiscal_period_end_abstains():
    payload = _payload([_row("2025-12-31", 643_000_000.0)])
    assert diluted_shares_from_concept(payload, []) == []


# =============================================================================
# Task 2: EdgarSource wiring — the fallback fires only when the statement view
# already came back diluted_shares == [], is isolated from the rest of
# Statements construction (C1), and resolves the CIK off the edgartools
# Company object (C2), never the raw ticker_map / a hardcoded CIK.
# =============================================================================

class _FakeStatement:
    def __init__(self, df):
        self._df = df

    def to_dataframe(self):
        return self._df


class _FakeFinancials:
    def __init__(self, inc, cf=None, bal=None, shares=None):
        self._inc = inc
        self._cf = cf if cf is not None else pd.DataFrame()
        self._bal = bal if bal is not None else pd.DataFrame()
        self._shares = shares

    def income_statement(self):
        return _FakeStatement(self._inc)

    def cashflow_statement(self):
        return _FakeStatement(self._cf)

    def balance_sheet(self):
        return _FakeStatement(self._bal)

    def get_shares_outstanding_diluted(self):
        return self._shares


_FY = ["2025-12-31 (FY)", "2024-12-31 (FY)", "2023-12-31 (FY)"]


def _income_no_share_row():
    """Root-cause-B shape: revenue/NI/diluted-EPS present, but NO diluted
    share-count row at any label or concept (CMCSA/CVX/GOOGL/HON/LMT/MO/MRK/PG)."""
    return pd.DataFrame([
        dict(zip(_FY, [100.0, 90.0, 80.0], strict=True),
             standard_concept="Revenue", label="r", concept="us-gaap_Revenues"),
        dict(zip(_FY, [10.0, 9.0, 8.0], strict=True),
             standard_concept="NetIncome", label="ni", concept="us-gaap_NetIncomeLoss"),
        dict(zip(_FY, [1.5, 1.4, 1.3], strict=True),
             standard_concept=float("nan"), label="Diluted (in dollars per share)",
             concept="us-gaap_EarningsPerShareDiluted"),
    ])


def _income_with_share_row():
    """Same as above but WITH a working diluted share-count row -- extraction
    already succeeds, so the fallback must never be consulted."""
    df = _income_no_share_row()
    extra = dict(zip(_FY, [700_000_000.0, 705_000_000.0, 710_000_000.0], strict=True),
                 standard_concept=float("nan"),
                 label="Weighted average shares outstanding, diluted",
                 concept="us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding")
    return pd.concat([df, pd.DataFrame([extra])], ignore_index=True)


def _concept_payload_matching_spine():
    return _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])


def _raise_if_called(self, ticker):
    raise AssertionError(f"_fetch_diluted_shares_concept must not be called for {ticker!r}")


def test_fallback_fires_and_populates_diluted_shares(monkeypatch):
    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    monkeypatch.setattr(EdgarSource, "_fetch_diluted_shares_concept",
                        lambda self, ticker: _concept_payload_matching_spine(), raising=False)

    fin = _FakeFinancials(_income_no_share_row())
    snap = src._build_financials_snapshot("HON", fin)

    assert snap.statements is not None
    assert snap.statements.diluted_shares == [643_000_000.0, 655_000_000.0, 668_000_000.0]
    # untouched siblings prove this was additive, not a rebuild
    assert snap.statements.revenue == [100.0, 90.0, 80.0]
    assert snap.statements.net_income == [10.0, 9.0, 8.0]


def _concept_payload_partial_spine():
    """Covers only 2 of the 3 spine years (2023-12-31 is deliberately absent). A
    WELL-FORMED payload -- no exception anywhere in the seam or the aggregator --
    but the all-or-nothing contract means the result must still be []."""
    return _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        # 2023-12-31 missing entirely
    ])


def test_fallback_partial_spine_coverage_abstains_at_the_wiring_level(monkeypatch):
    """[task-3 review] The 'valid payload, partial spine coverage -> abstain' path
    was pinned only in the aggregator's own unit tests
    (test_partial_coverage_two_of_three_years_abstains in this file) plus an ad hoc
    live check on XOM -- never at the EdgarSource wiring level. This IS the XOM
    kill-switch shape end to end: the seam succeeds and returns real, well-formed
    data, but that data doesn't cover every spine year, so diluted_shares must stay
    [] rather than surface a partial/holed series, and the rest of Statements must
    stay intact (this is not a failure-isolation path -- nothing raises)."""
    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    monkeypatch.setattr(EdgarSource, "_fetch_diluted_shares_concept",
                        lambda self, ticker: _concept_payload_partial_spine(), raising=False)

    fin = _FakeFinancials(_income_no_share_row())
    snap = src._build_financials_snapshot("HON", fin)

    assert snap.statements is not None
    assert snap.statements.diluted_shares == []
    # rest of Statements is untouched -- this is abstention, not failure isolation
    assert snap.statements.revenue == [100.0, 90.0, 80.0]
    assert snap.statements.net_income == [10.0, 9.0, 8.0]
    assert snap.statements.diluted_eps == [1.5, 1.4, 1.3]


def test_fallback_does_not_fire_when_extraction_already_has_values(monkeypatch):
    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    monkeypatch.setattr(EdgarSource, "_fetch_diluted_shares_concept", _raise_if_called,
                        raising=False)

    fin = _FakeFinancials(_income_with_share_row())
    snap = src._build_financials_snapshot("AAPL", fin)   # would raise via the double if called

    assert snap.statements.diluted_shares == [700_000_000.0, 705_000_000.0, 710_000_000.0]


def test_fallback_does_not_fire_when_fiscal_period_end_empty(monkeypatch):
    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    monkeypatch.setattr(EdgarSource, "_fetch_diluted_shares_concept", _raise_if_called,
                        raising=False)

    fin = _FakeFinancials(pd.DataFrame())   # no FY columns at all -> fiscal_period_end == []
    snap = src._build_financials_snapshot("ZZZ", fin)   # would raise via the double if called

    assert snap.statements is None


def test_seam_raising_leaves_diluted_shares_empty_and_rest_of_statements_intact(monkeypatch):
    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"

    def _boom(self, ticker):
        raise RuntimeError("SEC 503")

    monkeypatch.setattr(EdgarSource, "_fetch_diluted_shares_concept", _boom, raising=False)

    fin = _FakeFinancials(_income_no_share_row())
    snap = src._build_financials_snapshot("HON", fin)   # must not raise/propagate

    assert snap.statements is not None
    assert snap.statements.diluted_shares == []
    # C1: a raising fallback must never reduce the REST of statements' coverage.
    assert snap.statements.revenue == [100.0, 90.0, 80.0]
    assert snap.statements.net_income == [10.0, 9.0, 8.0]
    assert snap.statements.diluted_eps == [1.5, 1.4, 1.3]


def test_seam_resolves_cik_off_the_company_object(monkeypatch):
    """C2: the seam takes the TICKER and resolves Company(ticker).cik itself --
    never a raw ticker->CIK map (the XOM->2115436 fee-shell trap)."""
    seen = {}

    class _FakeCompany:
        def __init__(self, ticker):
            seen["ticker"] = ticker
            # XOM's real operating-company CIK (NOT the 2115436 fee-shell one a
            # raw company_tickers.json first-occurrence lookup would trap into).
            self.cik = 34088

    monkeypatch.setattr("edgar.Company", _FakeCompany)

    captured = {}

    class _FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"cik": 34088, "tag": "WeightedAverageNumberOfDilutedSharesOutstanding"}

    def _fake_get(url, **kw):
        captured["url"] = url
        captured["headers"] = kw.get("headers")
        captured["timeout"] = kw.get("timeout")
        return _FakeResponse()

    monkeypatch.setattr("httpx.get", _fake_get)

    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    src.identity = "test test@example.com"

    out = src._fetch_diluted_shares_concept("XOM")

    assert seen["ticker"] == "XOM"
    assert "CIK0000034088" in captured["url"]
    assert "2115436" not in captured["url"]
    assert captured["timeout"] is not None            # I1: an explicit bound is required
    assert captured["headers"]["User-Agent"] == "test test@example.com"
    assert out == {"cik": 34088, "tag": "WeightedAverageNumberOfDilutedSharesOutstanding"}


def test_seam_never_raises_on_company_resolution_failure(monkeypatch):
    class _BoomCompany:
        def __init__(self, ticker):
            raise RuntimeError("no such entity")

    monkeypatch.setattr("edgar.Company", _BoomCompany)

    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    src.identity = "test test@example.com"

    assert src._fetch_diluted_shares_concept("NOPE") == {}


def test_seam_never_raises_on_http_failure(monkeypatch):
    class _FakeCompany:
        def __init__(self, ticker):
            self.cik = 34088

    monkeypatch.setattr("edgar.Company", _FakeCompany)

    def _fake_get(url, **kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr("httpx.get", _fake_get)

    src = EdgarSource.__new__(EdgarSource)
    src.name = "edgar"
    src.identity = "test test@example.com"

    assert src._fetch_diluted_shares_concept("XOM") == {}
