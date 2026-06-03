from shortlist.backtest.xbrl import build_cik_index, cik_for

_RAW = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
}

def test_build_cik_index_and_lookup_is_case_insensitive():
    idx = build_cik_index(_RAW)
    assert cik_for("aapl", idx) == "0000320193"   # zero-padded to 10
    assert cik_for("MSFT", idx) == "0000789019"

def test_cik_for_unknown_ticker_returns_none():
    assert cik_for("NOPE", build_cik_index(_RAW)) is None

def test_build_cik_index_skips_malformed_rows():
    raw = {
        "0": {"cik_str": "not-a-number", "ticker": "BAD"},
        "1": {"cik_str": 789019, "ticker": "MSFT"},
    }
    idx = build_cik_index(raw)
    assert "BAD" not in idx
    assert idx["MSFT"] == "0000789019"


# tests/test_xbrl_fetch.py  (append)
import asyncio
from shortlist.backtest.xbrl import fetch_companyfacts

class _FakeResp:
    def __init__(self, payload): self._p = payload
    def raise_for_status(self): pass
    def json(self): return self._p

class _FakeClient:
    def __init__(self, payload): self.payload = payload; self.calls = 0
    async def get(self, url, *a, **k):
        self.calls += 1
        return _FakeResp(self.payload)

def test_fetch_companyfacts_caches_to_disk(tmp_path):
    payload = {"cik": 320193, "facts": {"us-gaap": {}}}
    client = _FakeClient(payload)
    out1 = asyncio.run(fetch_companyfacts(
        "0000320193", client, cache_dir=str(tmp_path), month="2026-06"))
    out2 = asyncio.run(fetch_companyfacts(
        "0000320193", client, cache_dir=str(tmp_path), month="2026-06"))
    assert out1 == payload and out2 == payload
    assert client.calls == 1   # second call served from disk
    assert (tmp_path / "CIK0000320193-2026-06.json").exists()
