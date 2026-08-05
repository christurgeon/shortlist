"""SEC XBRL `frames` leaf — one concept across EVERY filer in one request.

Measured 2026-08-05 (docs/audits/2026-08-05-standing-screen-data-source.md): a full-universe
fundamental snapshot costs ~12 requests / ~8 MB and is CURRENT, against DERA bulk's 127-215
day staleness and per-ticker companyfacts' ~4,620 requests / 3.8 GB.
"""
from datetime import date

import pytest

from shortlist.data.secframes import (Frame, frame_url, fetch_frame, merge_family,
                                      parse_frame)


def _payload(rows):
    return {"taxonomy": "us-gaap", "tag": "Assets", "ccp": "CY2026Q1I",
            "uom": "USD", "pts": len(rows),
            "data": [{"accn": a, "cik": c, "entityName": n, "loc": "US-CA",
                      "end": e, "val": v} for c, n, a, e, v in rows]}


def test_frame_url_shape():
    assert frame_url("Assets", "CY2026Q1I") == (
        "https://data.sec.gov/api/xbrl/frames/us-gaap/Assets/USD/CY2026Q1I.json")
    assert "dei/EntityCommonStockSharesOutstanding/shares/" in frame_url(
        "EntityCommonStockSharesOutstanding", "CY2026Q1I", ns="dei", unit="shares")


def test_parse_frame_keys_on_zero_padded_cik():
    """CIK must be zero-padded to 10 to join with cik_tickers/Symbology, which both use
    that form — a raw int key would silently miss every lookup."""
    f = parse_frame(_payload([(320193, "Apple", "acc-1", "2026-03-31", 350.0)]))
    assert f["0000320193"].val == 350.0
    assert f["0000320193"].end == "2026-03-31"


def test_parse_frame_skips_one_malformed_row_and_keeps_the_rest():
    """One bad row among thousands must never discard the whole frame (the
    build_cik_to_ticker precedent)."""
    raw = _payload([(320193, "Apple", "a", "2026-03-31", 350.0),
                    (789019, "MSFT", "b", "2026-03-31", 500.0)])
    raw["data"].append({"cik": None, "val": 1.0})          # no cik
    raw["data"].append({"cik": 42, "val": "not-a-number"})  # unparseable value
    raw["data"].append({"cik": 43})                         # no val at all
    f = parse_frame(raw)
    assert set(f) == {"0000320193", "0000789019"}


def test_parse_frame_degrades_to_empty_on_garbage():
    for junk in ({}, {"data": None}, {"data": "nope"}, []):
        assert parse_frame(junk) == {}


def test_merge_family_is_priority_first_wins():
    """Tag families are NOT summed — the first tag reporting a CIK owns it, mirroring
    _xbrl_facts.annual_series. Summing would double-count a filer that tags both."""
    a = {"0000000001": Frame(val=10.0, end="2025-12-31", accn="a")}
    b = {"0000000001": Frame(val=99.0, end="2025-12-31", accn="b"),
         "0000000002": Frame(val=20.0, end="2025-12-31", accn="c")}
    merged = merge_family([a, b])
    assert merged["0000000001"].val == 10.0     # first wins
    assert merged["0000000002"].val == 20.0     # later tag fills the gap


def test_merge_family_of_nothing_is_empty():
    assert merge_family([]) == {}
    assert merge_family([{}, {}]) == {}


# --- fetch: caching, throttle participation, never-raises -----------------------------

class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._p


class _Client:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.calls = payload, status, 0

    def get(self, url, **kw):
        self.calls += 1
        return _Resp(self.payload, self.status)


def test_fetch_frame_day_caches(tmp_path):
    c = _Client(_payload([(320193, "Apple", "a", "2026-03-31", 350.0)]))
    kw = dict(identity="me@x.com", cache_dir=str(tmp_path), today=date(2026, 8, 5),
              client=c, throttle=lambda n=None: None)
    assert fetch_frame("Assets", "CY2026Q1I", **kw)["0000320193"].val == 350.0
    assert fetch_frame("Assets", "CY2026Q1I", **kw)["0000320193"].val == 350.0
    assert c.calls == 1, "second call must be served from the day cache"


def test_fetch_frame_draws_on_the_shared_sec_budget(tmp_path):
    """data.sec.gov is SEC load like any other — it must be paced and COUNTED, or the
    per-consumer budget in RunManifest.sec_requests understates the run."""
    seen = []
    fetch_frame("Assets", "CY2026Q1I", identity="me@x.com", cache_dir=str(tmp_path),
                today=date(2026, 8, 5), client=_Client(_payload([])),
                throttle=lambda n=None: seen.append(n))
    assert seen == ["secframes"]


def test_fetch_frame_never_raises_and_never_caches_a_failure(tmp_path):
    """A 404/5xx must degrade to {} AND leave no cache file — caching a failure would
    pin the whole day to 'this concept does not exist'."""
    c = _Client({}, status=500)
    out = fetch_frame("Assets", "CY2026Q1I", identity="me@x.com", cache_dir=str(tmp_path),
                      today=date(2026, 8, 5), client=c, throttle=lambda n=None: None)
    assert out == {}
    assert list(tmp_path.iterdir()) == []
    fetch_frame("Assets", "CY2026Q1I", identity="me@x.com", cache_dir=str(tmp_path),
                today=date(2026, 8, 5), client=c, throttle=lambda n=None: None)
    assert c.calls == 2, "a failure must not be cached"


@pytest.mark.parametrize("frame", ["CY2026Q1I", "CY2025"])
def test_fetch_frame_cache_key_separates_frames(tmp_path, frame):
    c = _Client(_payload([(1, "X", "a", "2026-03-31", 1.0)]))
    fetch_frame("Assets", frame, identity="me@x.com", cache_dir=str(tmp_path),
                today=date(2026, 8, 5), client=c, throttle=lambda n=None: None)
    assert any(frame in p.name for p in tmp_path.iterdir())


# --- frame-period helpers -------------------------------------------------------------

from shortlist.data.secframes import annual_frames, instant_frames  # noqa: E402


def test_instant_frames_walk_back_from_the_last_COMPLETED_quarter():
    """A frame fills as filers report, so the newest quarter is always partial. Walking back
    lets an older, fuller frame backfill a filer missing from the newest one."""
    assert instant_frames(date(2026, 8, 5), n=3) == ["CY2026Q2I", "CY2026Q1I", "CY2025Q4I"]
    assert instant_frames(date(2026, 1, 15), n=2) == ["CY2025Q4I", "CY2025Q3I"]


def test_annual_frames_start_at_the_last_COMPLETE_year():
    assert annual_frames(date(2026, 8, 5), n=3) == ["CY2025", "CY2024", "CY2023"]


def test_frame_helpers_reject_nonsense_n():
    assert instant_frames(date(2026, 8, 5), n=0) == []
    assert annual_frames(date(2026, 8, 5), n=0) == []
