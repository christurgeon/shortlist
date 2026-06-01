from __future__ import annotations

from shortlist.models import Coverage, ScoreCard, StockMetrics


def _card(**over):
    base = dict(
        ticker="T", composite=1.0, quality=None, moat=None, growth=None, momentum=None,
        value=None, opportunity=None, insider=None, metrics=StockMetrics(ticker="T"),
    )
    base.update(over)
    return ScoreCard(**base)


def test_coverage_dataclass_holds_fields():
    cov = Coverage(providers={"fmp": "gated_402"}, unavailable=["value"], note="x")
    assert cov.providers == {"fmp": "gated_402"}
    assert cov.unavailable == ["value"]
    assert cov.note == "x"


def test_scorecard_coverage_defaults_to_none():
    assert _card().coverage is None


import requests

from shortlist.coverage import classify_failure


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(response=resp)


def test_classify_failure_402_is_gated():
    assert classify_failure(_http_error(402)) == "gated_402"


def test_classify_failure_other_http_is_error():
    assert classify_failure(_http_error(500)) == "error"


def test_classify_failure_non_http_is_error():
    assert classify_failure(RuntimeError("boom")) == "error"


from shortlist.coverage import build_coverage


def test_build_coverage_gated_fmp_lists_value_and_note():
    m = StockMetrics(ticker="SCHW")
    m.sources = {"price": "finnhub", "insider_net_6m": "edgar"}  # no fmp fields
    card = _card(ticker="SCHW", composite=43.2, quality=45.4, moat=50.0,
                 momentum=57.1, value=None, opportunity=57.1, insider=10.8, metrics=m)
    cov = build_coverage({"fmp": "gated_402", "finnhub": "ok", "edgar": "ok"},
                         {"finnhub", "edgar"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "gated_402"
    assert "value" in cov.unavailable
    assert "growth" in cov.unavailable  # income history is FMP-sourced; gated -> null
    assert "upside_to_target" in cov.unavailable  # price set but no target_median
    assert "Starter" in cov.note


def test_build_coverage_reclassifies_ok_but_empty_provider():
    # fmp did not raise but returned no fields -> not in `contributed` -> "empty".
    card = _card(ticker="X")
    cov = build_coverage({"fmp": "ok", "finnhub": "ok"}, {"finnhub"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "empty"


def test_build_coverage_provider_that_lost_on_priority_is_not_empty():
    """A provider that fetched data but won zero fields on merge priority must NOT
    be mislabeled 'empty' — contribution is judged by the provider's own fetch
    (`contributed`), not by which provider won each merged field."""
    m = StockMetrics(ticker="X")
    m.sources = {"market_cap": "fmp", "roe": "fmp"}  # fmp won every merged field
    card = _card(ticker="X", quality=80.0, metrics=m)
    # finnhub DID return data (so it's in `contributed`) but lost all fields to fmp:
    cov = build_coverage({"fmp": "ok", "finnhub": "ok"}, {"fmp", "finnhub"}, card)
    assert cov is None  # finnhub not falsely marked "empty"


def test_build_coverage_all_ok_returns_none():
    card = _card(ticker="X", quality=80.0)
    assert build_coverage({"fmp": "ok", "finnhub": "ok"}, {"fmp", "finnhub"}, card) is None


def test_build_coverage_handles_none_metrics():
    card = _card(ticker="X", metrics=None)
    cov = build_coverage({"fmp": "gated_402"}, set(), card)  # must not raise
    assert "upside_to_target" in cov.unavailable


from shortlist.coverage import coverage_note_line


def test_coverage_note_line_renders_flagged_providers():
    cov = Coverage(providers={"fmp": "gated_402", "finnhub": "ok"},
                   unavailable=["value", "upside_to_target"], note="x")
    line = coverage_note_line("SCHW", cov)
    assert "SCHW" in line
    assert "fmp gated (402)" in line
    assert "value, upside_to_target" in line
    assert "finnhub" not in line  # ok providers are not listed


from shortlist.coverage import _build_note


def test_build_coverage_error_only_returns_coverage_with_generic_note():
    """A provider that returns an HTTP 500 (error) while another succeeds should
    produce a Coverage with the error status and a generic note (not FMP Starter)."""
    card = _card(ticker="FH", quality=50.0)
    cov = build_coverage({"fmp": "ok", "finnhub": "error"}, {"fmp"}, card)
    assert cov is not None
    assert cov.providers["finnhub"] == "error"
    assert "Starter" not in (cov.note or "")
    # generic note must mention finnhub
    assert cov.note is not None
    assert "finnhub" in cov.note


def test_build_coverage_fmp_error_not_gated_produces_generic_note():
    """fmp with status 'error' (e.g. HTTP 500) must NOT trigger the FMP Starter note."""
    card = _card(ticker="X")
    cov = build_coverage({"fmp": "error", "finnhub": "ok"}, {"finnhub"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "error"
    assert "Starter" not in (cov.note or "")
    assert cov.note is not None
    assert "fmp" in cov.note


def test_coverage_note_line_error_only_no_dangling_arrow():
    """An error-only Coverage must render a clean line — no dangling '  -> '."""
    cov = Coverage(
        providers={"finnhub": "error", "fmp": "ok"},
        unavailable=["moat", "momentum"],
        note="some note",
    )
    line = coverage_note_line("FH", cov)
    assert "finnhub" in line
    assert "fetch error" in line
    assert "  -> " not in line  # no dangling arrow with empty flagged part


def test_coverage_note_line_error_label_present():
    """The rendered line must include the human label for 'error' status."""
    cov = Coverage(
        providers={"finnhub": "error"},
        unavailable=["moat"],
        note=None,
    )
    line = coverage_note_line("FH", cov)
    assert "finnhub fetch error" in line


def test_build_coverage_multi_provider_generic_note():
    """When multiple non-fmp providers are flagged the generic note lists both names."""
    card = _card(ticker="X")
    cov = build_coverage({"fmp": "ok", "finnhub": "empty", "edgar": "error"},
                         {"fmp"}, card)
    assert cov is not None
    assert cov.note is not None
    assert "finnhub" in cov.note
    assert "edgar" in cov.note
    assert "Starter" not in cov.note
    # The note must not point at a stderr line that an `empty` provider never logs.
    assert "see stderr" not in cov.note


from pathlib import Path

import yaml

from shortlist import screen
from shortlist.models import StockMetrics as SM


class _Resp:
    def __init__(self, status): self.status_code = status


class _Http402(Exception):
    def __init__(self): self.response = _Resp(402)


class _FakeFMP:
    name = "fmp"
    def fetch(self, t):
        if t == "GATED":
            raise _Http402()
        m = SM(ticker=t)
        m.market_cap = 1.0e10
        m.sources["market_cap"] = "fmp"
        return m


class _FakeFinnhub:
    name = "finnhub"
    def fetch(self, t):
        m = SM(ticker=t)
        m.market_cap = 2.0e10
        m.roe = 0.2
        m.sources["market_cap"] = "finnhub"
        m.sources["roe"] = "finnhub"
        return m


def _config():
    path = Path(__file__).resolve().parents[1] / "config.yaml"
    return yaml.safe_load(path.read_text())


def test_run_attaches_coverage_and_does_not_leak(monkeypatch):
    monkeypatch.setattr(screen, "build_providers",
                        lambda names: [_FakeFMP(), _FakeFinnhub()])
    cards = screen.run(["GATED", "OK"], ["dummy"], _config())
    by = {c.ticker: c for c in cards}
    # GATED: fmp raised 402 but finnhub succeeded -> card exists with coverage
    assert by["GATED"].coverage is not None
    assert by["GATED"].coverage.providers["fmp"] == "gated_402"
    # OK: both providers contributed -> no coverage; outcomes did NOT leak
    assert by["OK"].coverage is None


def test_card_dict_emits_coverage_when_present():
    cov = Coverage(providers={"fmp": "gated_402"}, unavailable=["value"], note="x")
    d = screen._card_dict(_card(coverage=cov))
    assert d["coverage"]["providers"]["fmp"] == "gated_402"
    assert d["coverage"]["unavailable"] == ["value"]
    assert d["coverage"]["note"] == "x"


def test_card_dict_omits_coverage_when_absent():
    assert "coverage" not in screen._card_dict(_card())
