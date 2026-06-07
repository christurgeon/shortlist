from datetime import date
from shortlist.scout.signals import build_signals, WsbHypeSignal
from shortlist.data import apewisdom


def _idx():
    mk = apewisdom.WsbMention
    return {
        apewisdom.norm_symbol("GME"): mk(ticker="GME", mentions=300, mentions_24h_ago=100,
                                         rank=1, mention_delta_pct=2.0, rising=True, as_of="2026-06-07"),
        apewisdom.norm_symbol("SPY"): mk(ticker="SPY", mentions=500, mentions_24h_ago=100,
                                         rank=2, mention_delta_pct=4.0, rising=True, as_of="2026-06-07"),
        apewisdom.norm_symbol("KO"):  mk(ticker="KO", mentions=10, mentions_24h_ago=2,
                                         rank=3, mention_delta_pct=4.0, rising=True, as_of="2026-06-07"),
        apewisdom.norm_symbol("F"):   mk(ticker="F", mentions=200, mentions_24h_ago=190,
                                         rank=4, mention_delta_pct=0.05, rising=True, as_of="2026-06-07"),
    }


def test_wsb_signal_emits_hyped_only(monkeypatch):
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (_idx(), None))
    sig = WsbHypeSignal(min_mentions=50, min_delta_pct=0.5, top_n=15, deny_list=["SPY"])
    ems = sig.scan(date(2026, 6, 7))
    tickers = {e.ticker for e in ems}
    assert "GME" in tickers          # 300 mentions, +200% -> qualifies
    assert "SPY" not in tickers      # deny-listed index ETF
    assert "KO" not in tickers       # below absolute mention floor (10 < 50)
    assert "F" not in tickers        # below velocity floor (+5% < +50%)
    assert all(e.is_discovery and e.signal == "wsb:hype" for e in ems)
    ran, detail = sig.available()
    assert ran is True


def test_wsb_signal_top_n_caps(monkeypatch):
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (_idx(), None))
    sig = WsbHypeSignal(min_mentions=1, min_delta_pct=0.0, top_n=1, deny_list=[])
    ems = sig.scan(date(2026, 6, 7))
    assert len(ems) == 1             # highest-velocity survivor only


def test_wsb_signal_never_raises_on_error(monkeypatch):
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: ({}, "boom"))
    sig = WsbHypeSignal()
    assert sig.scan(date(2026, 6, 7)) == []
    ran, detail = sig.available()
    assert ran is False and "boom" in detail


def test_wsb_signal_resolves_via_build_signals():
    sigs = build_signals(["wsb_hype"], {"wsb_hype": {"min_mentions": 5}})
    assert len(sigs) == 1 and sigs[0].name == "wsb_hype" and sigs[0].is_discovery is True


def test_wsb_signal_strength_clamps_to_one(monkeypatch):
    from shortlist.data import apewisdom
    mk = apewisdom.WsbMention
    idx = {apewisdom.norm_symbol("GME"): mk(ticker="GME", mentions=900, mentions_24h_ago=100,
                                            rank=1, mention_delta_pct=8.0, rising=True, as_of="2026-06-07")}
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (idx, None))
    sig = WsbHypeSignal(min_mentions=50, min_delta_pct=0.5, top_n=15, deny_list=[])
    ems = sig.scan(date(2026, 6, 7))
    assert len(ems) == 1
    assert ems[0].strength == 1.0          # +800% delta clamps to 1.0
    assert ems[0].signal == "wsb:hype"


def test_wsb_signal_deny_list_normalizes_dotted(monkeypatch):
    from shortlist.data import apewisdom
    mk = apewisdom.WsbMention
    idx = {apewisdom.norm_symbol("BRK.B"): mk(ticker="BRK.B", mentions=300, mentions_24h_ago=100,
                                              rank=1, mention_delta_pct=2.0, rising=True, as_of="2026-06-07")}
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (idx, None))
    sig = WsbHypeSignal(min_mentions=50, min_delta_pct=0.5, top_n=15, deny_list=["BRK-B"])
    ems = sig.scan(date(2026, 6, 7))
    assert ems == []                       # BRK-B deny entry matches BRK.B row after norm
