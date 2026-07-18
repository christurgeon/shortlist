"""EdgarStakeIncreaseSignal (scout/signals.py): fetch budgeting + wiring around the pure
edgar_index.stake_increases_from_records aggregator. Ships disabled at weight 0.5 pending
the pre-registered backfill verdict (preregister/edgar_13d_stake_increase.yaml)."""
from datetime import date

from shortlist.scout.signals import EdgarStakeIncreaseSignal


def _mk(monkeypatch, records, baselines=None, prior=None, **kw):
    sig = EdgarStakeIncreaseSignal(identity="id", baselines=baselines or {},
                                   seen_accessions=kw.pop("seen", []), **kw)
    monkeypatch.setattr(
        "shortlist.scout.signals.EdgarStakeIncreaseSignal._resolver_map",
        lambda self: {"0000000123": "TGT"})
    monkeypatch.setattr(
        "shortlist.scout.edgar_index.fetch_recent_amendment_records",
        lambda session, cap, ident, resolve, **k: (records, session))
    monkeypatch.setattr("shortlist.scout.signals._stake_from_filing",
                        lambda f: f and f.get("pct"))
    monkeypatch.setattr("shortlist.scout.signals._prior_stake",
                        lambda *a, **k: prior)
    return sig


def _rec(pct=8.0, acc="a1"):
    return {"ticker": "TGT", "cik": "0000000123", "filer_cik": "0000000900",
            "subject_name": "Target Co", "activist": "Fund LP",
            "form": "SCHEDULE 13D/A", "accession": acc,
            "file_date": "2026-07-10", "_filing": {"pct": pct}}


def test_emits_on_material_increase_with_state_baseline(monkeypatch):
    sig = _mk(monkeypatch, [_rec()],
              baselines={"0000000900|0000000123": {"pct": 5.0, "date": "2026-01-01"}})
    ems = sig.scan(date(2026, 7, 10))
    assert len(ems) == 1 and ems[0].meta["new_pct"] == 8.0
    assert sig.new_accessions == ["a1"]
    assert sig.baseline_updates["0000000900|0000000123"]["pct"] == 8.0
    ran, detail = sig.available()
    assert ran


def test_cold_start_uses_bounded_prior_fetch(monkeypatch):
    sig = _mk(monkeypatch, [_rec()], baselines={}, prior=5.0)
    ems = sig.scan(date(2026, 7, 10))
    assert len(ems) == 1 and ems[0].meta["prior_pct"] == 5.0


def test_prior_fetch_budget_overflow_is_named(monkeypatch):
    recs = [_rec(acc=f"a{i}") for i in range(3)]
    for i, r in enumerate(recs):
        r["cik"] = f"{123 + 0:010d}"                  # same subject; distinct filers
        r["filer_cik"] = f"{900 + i:010d}"
    sig = _mk(monkeypatch, recs, baselines={}, prior=5.0, max_prior_fetches=2)
    sig.scan(date(2026, 7, 10))
    ran, detail = sig.available()
    assert "prior-fetch budget" in detail            # overflow named, never silent


def test_seen_accessions_deduped(monkeypatch):
    sig = _mk(monkeypatch, [_rec()], seen=["a1"],
              baselines={"0000000900|0000000123": {"pct": 5.0, "date": "2026-01-01"}})
    assert sig.scan(date(2026, 7, 10)) == []


def test_never_raises_on_fetch_failure(monkeypatch):
    sig = EdgarStakeIncreaseSignal(identity="id")
    monkeypatch.setattr(
        "shortlist.scout.signals.EdgarStakeIncreaseSignal._resolver_map",
        lambda self: {"0000000123": "TGT"})

    def boom(*a, **k):
        raise RuntimeError("sec down")
    monkeypatch.setattr("shortlist.scout.edgar_index.fetch_recent_amendment_records", boom)
    assert sig.scan(date(2026, 7, 10)) == []
    ran, _ = sig.available()
    assert not ran
