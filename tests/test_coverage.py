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
