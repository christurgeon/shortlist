from datetime import date
from pathlib import Path

from shortlist.scout.signals import EdgarForm4Signal

FIX = Path(__file__).parent / "fixtures" / "form4"


def test_signal_emits_from_injected_submissions_without_network():
    xml = (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")
    sig = EdgarForm4Signal(
        cfg={"min_value": 100000, "roles": ["officer", "director"],
             "exclude_10b5_1": True,
             "tier_strength": {"opportunistic": 1.0, "unclassified": 0.6}},
        fetch_submissions=lambda session, cap: ([xml], session),
        load_index=dict,          # empty history -> unclassified tier
    )
    ems = sig.scan(date(2025, 3, 31))
    assert [e.ticker for e in ems] == ["OKLO"]
    assert ems[0].meta["tier"] == "unclassified"
    ok, detail = sig.available()
    assert ok and "1" in detail


def test_signal_degrades_quietly_when_the_fetch_fails():
    def boom(session, cap):
        raise RuntimeError("SEC 503")
    sig = EdgarForm4Signal(cfg={}, fetch_submissions=boom, load_index=dict)
    assert sig.scan(date(2025, 3, 31)) == []
    ok, detail = sig.available()
    assert ok is False and "503" in detail


def test_default_index_threads_cfg_dera_settings_and_identity(monkeypatch):
    """No `load_index` injected -> the signal must build the DERA history itself from
    cfg["dera"] (quarters/cache_dir), using session as the as-of anchor and self.identity."""
    calls = {}

    def fake_quarters_back(as_of, n):
        calls["quarters_back"] = (as_of, n)
        return ["2025q1"]

    def fake_load_index(cache_dir, quarters, identity):
        calls["load_index"] = (cache_dir, quarters, identity)
        return {}

    monkeypatch.setattr("shortlist.scout.dera.quarters_back", fake_quarters_back)
    monkeypatch.setattr("shortlist.scout.dera.load_index", fake_load_index)

    sig = EdgarForm4Signal(
        cfg={"dera": {"quarters": 4, "cache_dir": "/tmp/dera-test"}},
        identity="custom@x.z",
        fetch_submissions=lambda session, cap: ([], session),
    )
    assert sig.scan(date(2025, 3, 31)) == []
    assert calls["quarters_back"] == (date(2025, 3, 31), 4)
    assert calls["load_index"] == ("/tmp/dera-test", ["2025q1"], "custom@x.z")


def test_removing_the_form4_block_leaves_the_signal_inert():
    """Convention: an empty cfg (equivalent to no scout.form4 block) still degrades honestly
    -- an injected empty fetch never emits, regardless of cfg contents."""
    sig = EdgarForm4Signal(cfg={}, fetch_submissions=lambda s, c: ([], s),
                           load_index=dict)
    assert sig.scan(date(2025, 6, 2)) == []
