import json
from pathlib import Path

import httpx
import pytest

from shortlist.edgar import symbology
from shortlist.edgar.symbology import _raw_snapshot, snapshot_map, snapshot_reverse

_RAW = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 886158, "ticker": "BBBY", "title": "BED BATH & BEYOND INC"}}


def _seed_cache(cache_dir, ts):
    p = Path(cache_dir) / "wayback_tickers" / f"{ts}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_RAW))


def test_snapshot_map_reads_cache_no_network(tmp_path):
    _seed_cache(tmp_path, "20221003133031")
    m = snapshot_map("20221003133031", cache_dir=str(tmp_path), client=None)  # client unused on cache hit
    assert m["0000886158"] == "BBBY" and m["0000320193"] == "AAPL"


def test_snapshot_reverse_ticker_to_cik(tmp_path):
    _seed_cache(tmp_path, "20221003133031")
    r = snapshot_reverse("20221003133031", cache_dir=str(tmp_path))
    assert r["BBBY"] == 886158 and r["AAPL"] == 320193
    assert "GONE" not in r


def test_snapshot_map_missing_cache_no_client_degrades(tmp_path):
    # no cache + no client -> must not raise, returns {}
    assert snapshot_map("20180101000000", cache_dir=str(tmp_path), client=None) == {}


def test_snapshot_reverse_non_dict_json_degrades(tmp_path, monkeypatch):
    # a truthy non-dict JSON payload must degrade to {} (never raise .values())
    monkeypatch.setattr(symbology, "_raw_snapshot", lambda *a, **k: ["not", "a", "dict"])
    assert snapshot_reverse("20221003133031", cache_dir=str(tmp_path)) == {}


def test_snapshot_reverse_skips_none_ticker(tmp_path, monkeypatch):
    raw = {"0": {"cik_str": 320193, "ticker": "AAPL"},
           "1": {"cik_str": 999, "ticker": None},
           "2": {"cik_str": 888}}  # missing ticker
    monkeypatch.setattr(symbology, "_raw_snapshot", lambda *a, **k: raw)
    r = snapshot_reverse("20221003133031", cache_dir=str(tmp_path))
    assert r == {"AAPL": 320193}
    assert "NONE" not in r


def _fast_fetch(monkeypatch):
    # keep the retry/backoff test instant: no throttle wait, no backoff sleep
    monkeypatch.setattr(symbology, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(symbology.time, "sleep", lambda *a, **k: None)


def test_raw_snapshot_200_returns_json_and_caches(tmp_path, monkeypatch):
    _fast_fetch(monkeypatch)
    hits = {"n": 0}

    def handler(request):
        hits["n"] += 1
        return httpx.Response(200, json=_RAW)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raw = _raw_snapshot("20221003133031", cache_dir=str(tmp_path), client=client)
    assert raw == _RAW
    assert hits["n"] == 1  # succeeded on first attempt
    cp = Path(tmp_path) / "wayback_tickers" / "20221003133031.json"
    assert cp.exists()
    assert json.loads(cp.read_text()) == _RAW


def test_raw_snapshot_non_200_returns_none_and_does_not_cache(tmp_path, monkeypatch):
    _fast_fetch(monkeypatch)

    def handler(request):
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.warns(UserWarning):
        raw = _raw_snapshot("20180101000000", cache_dir=str(tmp_path), client=client)
    assert raw is None
    cp = Path(tmp_path) / "wayback_tickers" / "20180101000000.json"
    assert not cp.exists()  # only a 200 is ever cached


def test_raw_snapshot_repeated_failure_warns(tmp_path, monkeypatch):
    _fast_fetch(monkeypatch)
    attempts = {"n": 0}

    def handler(request):
        attempts["n"] += 1
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.warns(UserWarning, match="snapshot fetch failed after retries"):
        raw = _raw_snapshot("20180101000000", cache_dir=str(tmp_path), client=client)
    assert raw is None
    assert attempts["n"] == 3  # exactly 3 attempts, no more
