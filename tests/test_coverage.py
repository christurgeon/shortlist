from __future__ import annotations

from shortlist.models import Coverage, ScoreCard, StockMetrics


def _card(**over):
    base = dict(
        ticker="T", composite=1.0, quality=None, moat=None, momentum=None,
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
    cov = build_coverage({"fmp": "gated_402", "finnhub": "ok", "edgar": "ok"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "gated_402"
    assert "value" in cov.unavailable
    assert "upside_to_target" in cov.unavailable  # price set but no target_median
    assert "Starter" in cov.note


def test_build_coverage_reclassifies_ok_but_empty_provider():
    m = StockMetrics(ticker="X")
    m.sources = {"price": "finnhub"}  # fmp contributed nothing despite not raising
    card = _card(ticker="X", metrics=m)
    cov = build_coverage({"fmp": "ok", "finnhub": "ok"}, card)
    assert cov is not None
    assert cov.providers["fmp"] == "empty"


def test_build_coverage_all_ok_returns_none():
    m = StockMetrics(ticker="X")
    m.sources = {"roe": "fmp", "price": "finnhub"}  # both contributed
    card = _card(ticker="X", quality=80.0, metrics=m)
    assert build_coverage({"fmp": "ok", "finnhub": "ok"}, card) is None


def test_build_coverage_handles_none_metrics():
    card = _card(ticker="X", metrics=None)
    cov = build_coverage({"fmp": "gated_402"}, card)  # must not raise
    assert "upside_to_target" in cov.unavailable
