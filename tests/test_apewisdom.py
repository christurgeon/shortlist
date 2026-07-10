from shortlist.data.apewisdom import parse_wsb, norm_symbol

_PAYLOAD = {
    "count": 3, "pages": 1, "current_page": 1,
    "results": [
        {"rank": 1, "ticker": "GME", "name": "GameStop", "mentions": 300,
         "upvotes": 900, "rank_24h_ago": 5, "mentions_24h_ago": 100},
        {"rank": 2, "ticker": "SPY", "name": "SPDR", "mentions": 102,
         "upvotes": 306, "rank_24h_ago": 2, "mentions_24h_ago": 117},
        {"rank": 3, "ticker": "", "name": "blank", "mentions": 9,
         "upvotes": 1, "rank_24h_ago": 9, "mentions_24h_ago": None},
    ],
}


def test_parse_derives_delta_and_rising():
    idx = parse_wsb(_PAYLOAD, as_of="2026-06-07")
    gme = idx[norm_symbol("GME")]
    assert gme.ticker == "GME"
    assert gme.mentions == 300 and gme.mentions_24h_ago == 100
    assert gme.mention_delta_pct == 2.0          # (300-100)/100
    assert gme.rising is True
    assert gme.as_of == "2026-06-07"


def test_parse_falling_name_not_rising():
    idx = parse_wsb(_PAYLOAD, as_of="2026-06-07")
    spy = idx[norm_symbol("SPY")]
    assert spy.rising is False                    # 102 < 117
    assert round(spy.mention_delta_pct, 4) == round((102 - 117) / 117, 4)


def test_parse_skips_blank_ticker_and_handles_none_prev():
    idx = parse_wsb(_PAYLOAD, as_of="2026-06-07")
    assert "" not in idx and norm_symbol("") not in idx
    assert len(idx) == 2


def test_parse_empty_payload_is_empty():
    assert parse_wsb(None, as_of="2026-06-07") == {}
    assert parse_wsb({"results": []}, as_of="2026-06-07") == {}


def test_parse_malformed_payload_never_raises():
    # non-dict payload, non-list `results`, and non-dict row all degrade safely
    assert parse_wsb("garbage", as_of="2026-06-07") == {}
    assert parse_wsb({"results": {"x": 1}}, as_of="2026-06-07") == {}
    # a stray non-dict row is skipped, valid rows still parse
    idx = parse_wsb({"results": ["notadict", {"ticker": "GME", "mentions": 5,
                                              "mentions_24h_ago": 1}]}, as_of="2026-06-07")
    assert norm_symbol("GME") in idx and len(idx) == 1


def test_parse_zero_prev_yields_none_delta():
    payload = {"results": [{"rank": 1, "ticker": "AMC", "mentions": 50,
                            "mentions_24h_ago": 0, "upvotes": 5, "rank_24h_ago": 1}]}
    amc = parse_wsb(payload, as_of="2026-06-07")[norm_symbol("AMC")]
    assert amc.mention_delta_pct is None    # no ZeroDivisionError; 0 prev -> None
    assert amc.rising is True               # 50 > 0


def test_norm_symbol_collapses_separators():
    assert norm_symbol("BRK.B") == norm_symbol("BRK-B") == "BRKB"


def test_fetch_reads_disk_cache_without_network(tmp_path, monkeypatch):
    # Pre-seed today's cache file; fetch must parse it and make ZERO network calls.
    import json
    from shortlist.data.apewisdom import fetch_wsb_mentions, _today_iso

    def _boom(*a, **k):
        raise AssertionError("network call attempted on a cache hit")
    monkeypatch.setattr("httpx.get", _boom)

    payload = {"results": [{"rank": 1, "ticker": "GME", "mentions": 300,
                            "mentions_24h_ago": 100, "upvotes": 9, "rank_24h_ago": 5}]}
    cache_dir = tmp_path / "ape"
    cache_dir.mkdir()
    (cache_dir / f"{_today_iso()}.json").write_text(
        json.dumps({"as_of": "2026-06-07", "payload": payload}))
    idx, err = fetch_wsb_mentions(cache_dir=str(cache_dir))
    assert err is None
    assert idx[norm_symbol("GME")].mentions == 300
    assert idx[norm_symbol("GME")].as_of == "2026-06-07"


def test_fetch_live_path_writes_cache_and_parses(tmp_path, monkeypatch):
    # On a cache miss, fetch GETs, writes the cache, and parses the response.
    import json
    from shortlist.data.apewisdom import fetch_wsb_mentions, _today_iso

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [{"rank": 1, "ticker": "GME", "mentions": 7,
                                 "mentions_24h_ago": 2}]}

    calls = {"n": 0}
    def _fake_get(url, **k):
        calls["n"] += 1
        return _Resp()
    monkeypatch.setattr("httpx.get", _fake_get)

    cache_dir = tmp_path / "ape"
    idx, err = fetch_wsb_mentions(cache_dir=str(cache_dir))
    assert err is None and calls["n"] == 1
    assert idx[norm_symbol("GME")].mentions == 7
    # the cache file was written with {as_of, payload}
    cached = json.loads((cache_dir / f"{_today_iso()}.json").read_text())
    assert cached["payload"]["results"][0]["ticker"] == "GME"


def test_fetch_network_error_returns_empty_and_error(tmp_path, monkeypatch):
    # A network failure NEVER raises — returns ({}, error_string).
    from shortlist.data.apewisdom import fetch_wsb_mentions
    def _raise(*a, **k):
        raise RuntimeError("connection refused")
    monkeypatch.setattr("httpx.get", _raise)
    idx, err = fetch_wsb_mentions(cache_dir=str(tmp_path / "ape"))
    assert idx == {} and err is not None and "connection refused" in err
