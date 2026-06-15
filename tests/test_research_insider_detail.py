import yaml
from pathlib import Path

from shortlist.research.assess import _insider_line, _build_user_prompt
from shortlist.research.models import FilingBundle, FilingText
from shortlist.data.models import TickerSnapshot, Insider, InsiderTxn
from shortlist.data.bridge import snapshot_to_metrics


CFG_ON = {"enabled": True, "max_items": 6}

_TRADES = [
    {"date": "2026-05-01", "name": "Jane Doe", "role": "CEO", "kind": "buy", "value": 2_100_000.0},
    {"date": "2026-04-15", "name": "John Roe", "role": "Director", "kind": "sell", "value": 500_000.0},
]


# --- _insider_line (pure) ---

def test_line_renders_role_name_verb_amount_date():
    line = _insider_line(_TRADES, CFG_ON)
    assert "CEO Jane Doe bought $2.1M (2026-05-01)" in line
    assert "Director John Roe sold $0.5M (2026-04-15)" in line
    assert "Form 4 derived" in line


def test_line_omitted_when_disabled_or_empty():
    assert _insider_line(_TRADES, {"enabled": False, "max_items": 6}) == ""
    assert _insider_line(_TRADES, None) == ""
    assert _insider_line([], CFG_ON) == ""
    assert _insider_line(None, CFG_ON) == ""


def test_line_respects_max_items():
    many = [dict(t, date=f"2026-01-0{i}") for i, t in enumerate([_TRADES[0]] * 9, 1)]
    line = _insider_line(many, {"enabled": True, "max_items": 3})
    assert line.count("bought") == 3


def test_line_none_safe_fields():
    line = _insider_line([{"date": None, "name": None, "role": None,
                           "kind": None, "value": None}], CFG_ON)
    assert "insider traded" in line          # graceful fallbacks, no crash


# --- prompt assembly ---

def _bundle():
    tenk = FilingText("AAPL", "acc", "2026-05-31", business="biz", mda="mda", risk_factors="rf")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-05-31")


def test_prompt_includes_insider_line_when_enabled():
    p = _build_user_prompt(_bundle(), {"research": {"insider_detail": CFG_ON}},
                           card=None, insider_recent=_TRADES)
    assert "Recent insider trades" in p and "CEO Jane Doe bought" in p


def test_prompt_omits_insider_line_when_disabled():
    p = _build_user_prompt(_bundle(), {"research": {"insider_detail": {"enabled": False}}},
                           card=None, insider_recent=_TRADES)
    assert "Recent insider trades" not in p


def test_prompt_omits_insider_line_when_config_absent():
    p = _build_user_prompt(_bundle(), {"research": {}}, card=None, insider_recent=_TRADES)
    assert "Recent insider trades" not in p


def test_insider_line_not_in_haystack():
    bundle = _bundle()
    assert "Recent insider trades" not in bundle.haystack()   # prompt-only, never grounded


# --- bridge derivation ---

def test_bridge_compacts_insider_recent():
    snap = TickerSnapshot(ticker="AAPL")
    snap.insider = Insider(net_value_6m=1.0, recent=[
        InsiderTxn(date="2026-05-01", name="Jane Doe", role="CEO", kind="buy",
                   shares=1000, price=210.0, value=2_100_000.0)])
    m = snapshot_to_metrics(snap)
    assert m.insider_recent == [{"date": "2026-05-01", "name": "Jane Doe",
                                 "role": "CEO", "kind": "buy", "value": 2_100_000.0}]


def test_bridge_no_recent_or_no_insider_leaves_none():
    snap = TickerSnapshot(ticker="AAPL")
    snap.insider = Insider(net_value_6m=1.0, recent=[])
    assert snapshot_to_metrics(snap).insider_recent is None
    assert snapshot_to_metrics(TickerSnapshot(ticker="KO")).insider_recent is None


def test_config_has_insider_detail_block():
    cfg = yaml.safe_load(Path("config.yaml").read_text())
    assert cfg["research"]["insider_detail"]["enabled"] is True
    assert cfg["research"]["insider_detail"]["max_items"] >= 1
