from shortlist.bot import monitor as mon


def _veto(items, adsh="0001-23-01", date="2026-07-19"):
    return {"items": list(items), "adsh": adsh, "last_date": date}


def _positions(*tickers):
    return {"version": 1, "positions": {
        t: {"added": "2026-01-01", "shares": 10, "thesis": None, "entry_card": None}
        for t in tickers}}


def test_alert_fires_for_held_ticker_with_subset_item():
    pos = _positions("NVDA")
    vm = {"NVDA": _veto(["4.02"])}
    alerts = mon.compute_alerts(pos["positions"], vm, mon.DEFAULT_ITEMS, set())
    assert len(alerts) == 1
    a = alerts[0]
    assert a["ticker"] == "NVDA" and a["kind"] == "8k_negative"
    assert a["key"] == "8k:0001-23-01"
    assert "relied on" in a["meaning"]           # plain-English gloss for 4.02

def test_no_alert_for_unheld_ticker():
    vm = {"MSFT": _veto(["4.02"])}
    assert mon.compute_alerts(_positions("NVDA")["positions"], vm, mon.DEFAULT_ITEMS, set()) == []

def test_non_subset_item_is_filtered():
    # 5.01 (change of control) is in the veto set but NOT the monitor subset
    vm = {"NVDA": _veto(["5.01"])}
    assert mon.compute_alerts(_positions("NVDA")["positions"], vm, mon.DEFAULT_ITEMS, set()) == []

def test_seen_key_is_deduped():
    vm = {"NVDA": _veto(["1.03"], adsh="AAA")}
    seen = {"8k:AAA"}
    assert mon.compute_alerts(_positions("NVDA")["positions"], vm, mon.DEFAULT_ITEMS, seen) == []

def test_thesis_carried_into_alert():
    pos = _positions("NVDA")
    pos["positions"]["NVDA"]["thesis"] = "capex cycle"
    vm = {"NVDA": _veto(["2.04"])}
    assert mon.compute_alerts(pos["positions"], vm, mon.DEFAULT_ITEMS, set())[0]["thesis"] == "capex cycle"

def test_heartbeat_counts_positions():
    hb = mon.heartbeat(_positions("NVDA", "MSFT")["positions"], "2026-07-22")
    assert hb == {"count": 2, "as_of": "2026-07-22"}

def test_all_default_items_have_meanings():
    for it in mon.DEFAULT_ITEMS:
        assert it in mon.ITEM_MEANINGS and mon.ITEM_MEANINGS[it]
