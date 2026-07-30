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
        fetch_submissions=lambda session, cap: ([xml], session, 1),
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
        return {}, 0

    monkeypatch.setattr("shortlist.scout.dera.quarters_back", fake_quarters_back)
    monkeypatch.setattr("shortlist.scout.dera.load_index", fake_load_index)

    sig = EdgarForm4Signal(
        cfg={"dera": {"quarters": 4, "cache_dir": "/tmp/dera-test"}},
        identity="custom@x.z",
        fetch_submissions=lambda session, cap: ([], session, 0),
    )
    assert sig.scan(date(2025, 3, 31)) == []
    assert calls["quarters_back"] == (date(2025, 3, 31), 4)
    assert calls["load_index"] == ("/tmp/dera-test", ["2025q1"], "custom@x.z")


def test_removing_the_form4_block_leaves_the_signal_inert():
    """C-2 contract: cfg=None (no scout.form4 block) must stop scan() from fetching or
    building/downloading the DERA index AT ALL -- not just from emitting.

    Deliberately uses RAISING fetch/load_index doubles: a plain empty-return double (the
    old version of this test) would pass even against a signal that still calls both and
    merely happens to produce no emissions from empty input -- a tautology that would not
    fail if the config-absence short-circuit were removed. This one only passes if `scan()`
    returns before either injection point is ever invoked."""
    def boom_fetch(session, cap):
        raise AssertionError("fetch must not be called when scout.form4 is absent")

    def boom_index():
        raise AssertionError("DERA index must not be built when scout.form4 is absent")

    sig = EdgarForm4Signal(cfg=None, fetch_submissions=boom_fetch, load_index=boom_index)
    assert sig.scan(date(2025, 6, 2)) == []
    ok, detail = sig.available()
    assert ok is False
    assert detail == "no scout.form4 config"


def test_present_but_empty_cfg_still_runs_on_code_defaults():
    """The other half of the C-2 contract: cfg={} (a present-but-empty block) is NOT the
    same as cfg=None -- it must still fetch/build the index and qualify on the hardcoded
    fallbacks in insider.qualifies/emissions_from_txns."""
    xml = (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")
    sig = EdgarForm4Signal(cfg={}, fetch_submissions=lambda s, c: ([xml], s, 1),
                           load_index=dict)
    ems = sig.scan(date(2025, 3, 31))
    assert [e.ticker for e in ems] == ["OKLO"]   # ran, qualified on code defaults
    ok, _detail = sig.available()
    assert ok is True


def test_status_distinguishes_sec_failure_from_a_quiet_day():
    """I-1: production's fetch_form4_submissions returns ([], None) (used=None) on a real
    SEC outage -- it degrades honestly rather than raising, per its own contract. That must
    be visibly distinguishable in available() from a genuinely quiet day, which returns
    ([], session) instead (used == session)."""
    sig = EdgarForm4Signal(cfg={}, fetch_submissions=lambda session, cap: ([], None, 0),
                           load_index=dict)
    assert sig.scan(date(2025, 3, 31)) == []
    ok, detail = sig.available()
    assert ok is False
    assert "SEC fetch failed" in detail


def test_emission_overflow_past_daily_cap_is_named_not_dropped_silently():
    """I-2: spec §5.1's own "never dropped silently" rule + the buyback signal's overflow-
    naming precedent both apply to the post-qualification daily_cap truncation too."""
    base = (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")
    xmls = [base.replace("OKLO", tkr) for tkr in ("AAA", "BBB", "CCC")]

    sig = EdgarForm4Signal(
        cfg={"min_value": 100000, "daily_cap": 1,
             "tier_strength": {"opportunistic": 1.0, "unclassified": 0.6}},
        fetch_submissions=lambda session, cap: (xmls, session, len(xmls)),
        load_index=dict,
    )
    ems = sig.scan(date(2025, 3, 31))
    assert len(ems) == 1
    ok, detail = sig.available()
    assert ok
    assert "2 emission(s) over daily_cap" in detail
    kept = ems[0].ticker
    overflowed = {"AAA", "BBB", "CCC"} - {kept}
    for tkr in overflowed:
        assert tkr in detail


def test_available_marks_truncation_when_fetch_count_reaches_the_cap():
    """When the fetched-doc count reaches max_filings, the day's filings were truncated in
    EDGAR index order -- an uncharacterizable sampling bias that must be visibly distinct
    from a quiet day, not just a repeat of the same "(cap N)" number every run."""
    xml = (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")
    sig = EdgarForm4Signal(
        cfg={}, max_filings=3,
        fetch_submissions=lambda session, cap: ([xml, xml, xml], session, 3),
        load_index=dict,
    )
    sig.scan(date(2025, 3, 31))
    ok, detail = sig.available()
    assert ok
    assert "TRUNCATED" in detail
    assert "cap 3" in detail


def test_available_does_not_mark_truncation_when_fetch_count_is_below_the_cap():
    xml = (FIX / "oklo_0001104659-25-030072.xml").read_text(errors="replace")
    sig = EdgarForm4Signal(
        cfg={}, max_filings=100,
        fetch_submissions=lambda session, cap: ([xml], session, 1),
        load_index=dict,
    )
    sig.scan(date(2025, 3, 31))
    ok, detail = sig.available()
    assert ok
    assert "TRUNCATED" not in detail
