from shortlist.backtest.universe import load_universe


def test_load_bundled_largecap():
    u = load_universe("largecap")
    assert len(u) >= 50
    assert "AAPL" in u and "MSFT" in u
    assert all(t == t.upper() for t in u)
    assert len(u) == len(set(u))            # no dups


def test_load_adhoc_csv():
    assert load_universe("gev,lmt , schw") == ["GEV", "LMT", "SCHW"]
