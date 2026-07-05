from datetime import date

from shortlist.scout.symbology import Symbology


def _sym(live, snaps, snapmaps, multimaps=None):
    """Construct a Symbology with injected maps (no network). Sets EVERY field __init__ sets,
    so the __new__ seam can't diverge from the real object (M1)."""
    s = Symbology.__new__(Symbology)
    s._live = live                                  # {cik10: ticker}
    s._snapshots = snaps                            # [(ts, date)]
    s._snap_cache = dict(snapmaps)                   # {ts: {cik10: ticker}}
    s._rev_cache = {}
    s._multi_cache = dict(multimaps or {})           # {ts: set(cik10 with >1 ticker)}
    s._cache_dir = "/unused"
    s._client = None
    s._owns_client = False
    s._overrides = {}
    s.disagreements = []
    s.low_confidence = []
    return s


def test_active_cik_resolves_from_live_and_logs_disagreement():
    s = _sym(live={"0000732712": "VZ"},
             snaps=[("20191002224708", date(2019, 10, 2))],
             snapmaps={"20191002224708": {"0000732712": "VZA"}})  # 2019 convention bug
    # active CIK -> LIVE ticker (VZ), NOT the buggy 2019 snapshot (VZA); disagreement logged
    assert s.resolve_ticker(732712, date(2019, 11, 1)) == "VZ"
    assert s.disagreements == [("0000732712", "VZ", "VZA")]


def test_delisted_cik_resolves_from_snapshot():
    s = _sym(live={},                                # 886158 scrubbed from live (delisted)
             snaps=[("20221003133031", date(2022, 10, 3))],
             snapmaps={"20221003133031": {"0000886158": "BBBY"}})
    assert s.resolve_ticker(886158, date(2022, 11, 15)) == "BBBY"


def test_no_coverage_returns_none():
    s = _sym(live={}, snaps=[("20221003133031", date(2022, 10, 3))],
             snapmaps={"20221003133031": {}})
    assert s.resolve_ticker(999999, date(2022, 11, 15)) is None
    # event before any snapshot AND not in live -> None
    assert s.resolve_ticker(886158, date(2015, 1, 1)) is None


def test_override_wins():
    s = _sym(live={"0000000001": "WRONG"}, snaps=[], snapmaps={})
    s._overrides = {"0000000001": "RIGHT"}
    assert s.resolve_ticker(1, date(2020, 1, 1)) == "RIGHT"


def test_malformed_cik_returns_none_never_raises():          # M2
    s = _sym(live={}, snaps=[], snapmaps={})
    assert s.resolve_ticker(None, date(2020, 1, 1)) is None
    assert s.resolve_ticker("not-a-cik", date(2020, 1, 1)) is None


def test_delisted_multiclass_flagged_low_confidence():        # C2
    # delisted CIK, archived ticker looks like a warrant sibling (ends 'W') -> low-confidence
    s = _sym(live={}, snaps=[("20190102000000", date(2019, 1, 2))],
             snapmaps={"20190102000000": {"0000000042": "FOOW"}})
    assert s.resolve_ticker(42, date(2019, 6, 1)) == "FOOW"   # still returned (best effort)
    assert ("0000000042", "FOOW") in s.low_confidence         # but flagged for spot-check


def test_delisted_multiticker_cik_flagged_via_multi_set():    # C2 (>1-candidate path)
    s = _sym(live={}, snaps=[("20190102000000", date(2019, 1, 2))],
             snapmaps={"20190102000000": {"0000000043": "FOO"}},
             multimaps={"20190102000000": {"0000000043"}})     # CIK had >1 ticker in snapshot
    assert s.resolve_ticker(43, date(2019, 6, 1)) == "FOO"
    assert ("0000000043", "FOO") in s.low_confidence


def test_init_owns_client_when_none(monkeypatch, tmp_path):   # C1 — the fix, locked
    import shortlist.scout.symbology as sym
    monkeypatch.setattr(sym, "load_cik_to_ticker", lambda *a, **k: {})
    monkeypatch.setattr(sym, "cdx_snapshots", lambda **k: [])
    s = sym.Symbology("test@example.com", cache_dir=str(tmp_path))   # client=None
    try:
        assert s._client is not None and s._owns_client is True
    finally:
        s.close()


def test_fetch_path_resolves_delisted_via_client(monkeypatch, tmp_path):  # C1 — wiring end-to-end
    import httpx, shortlist.scout.symbology as sym

    def handler(request):
        u = str(request.url)
        if "/cdx/" in u:
            return httpx.Response(200, json=[
                ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
                ["k", "20221003133031", "o", "application/json", "200", "d", "1"]])
        if "company_tickers.json" in u:   # the id_ raw snapshot fetch
            return httpx.Response(200, json={"0": {"cik_str": 886158, "ticker": "BBBY", "title": "B"}})
        return httpx.Response(404)

    monkeypatch.setattr(sym, "load_cik_to_ticker", lambda *a, **k: {})   # live map empty -> delisted path
    client = httpx.Client(transport=httpx.MockTransport(handler))
    s = sym.Symbology("test@example.com", cache_dir=str(tmp_path), client=client)
    # delisted CIK resolves via the fetched snapshot blob (the C1 bug would return None here)
    assert s.resolve_ticker(886158, date(2022, 11, 15)) == "BBBY"
