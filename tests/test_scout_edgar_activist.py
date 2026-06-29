from datetime import date

from shortlist.scout.edgar_index import (activist_stakes_from_records,
                                         fetch_recent_activist_records,
                                         _dedup_by_accession)


def _rec(ticker, activist, subject="Acme Inc", form="SCHEDULE 13D", acc=None, cik="1"):
    return {"ticker": ticker, "cik": cik, "subject_name": subject,
            "activist": activist, "form": form, "accession": acc or f"{ticker}-{activist}"}


def test_one_emission_per_ticker_with_cofiler_count():
    recs = [_rec("XYZ", "Fund A", acc="a1"), _rec("XYZ", "Fund B", acc="a2")]
    ems = activist_stakes_from_records(recs)
    assert len(ems) == 1
    assert ems[0].ticker == "XYZ"
    assert "2" in ems[0].evidence            # "2 activists" / "+1 co-filer"
    assert ems[0].signal == "edgar:activist_13d"
    assert ems[0].is_discovery is True


def test_placeholder_ticker_skipped():
    recs = [_rec("", "Fund A"), _rec("NONE", "Fund B")]
    assert activist_stakes_from_records(recs) == []


def test_amendment_excluded():
    recs = [_rec("XYZ", "Fund A", form="SCHEDULE 13D/A")]
    assert activist_stakes_from_records(recs) == []


def test_drop_spacs_and_affiliates():
    spac = _rec("PECE", "Sponsor LP", subject="Peace Acquisition Corp.")
    aff = _rec("HWKE", "Hawkeye HoldCo LLC", subject="Hawkeye Systems, Inc.")
    keep = _rec("TBBB", "Outside Capital Mgmt", subject="BBB Foods Inc")
    ems = activist_stakes_from_records([spac, aff, keep])
    assert [e.ticker for e in ems] == ["TBBB"]


def test_marquee_boosts_strength_and_labels():
    base = activist_stakes_from_records([_rec("AAA", "Random Holdings")])[0]
    marq = activist_stakes_from_records([_rec("BBB", "Elliott Investment Management L.P.")])[0]
    assert marq.strength > base.strength
    assert "Elliott" in marq.evidence


def test_mktcap_floor_callback_drops_subfloor():
    recs = [_rec("BIG", "Outside Cap Co"), _rec("SMALL", "Outside Cap Co")]
    ems = activist_stakes_from_records(recs, mktcap_floor_ok=lambda t: t == "BIG")
    assert [e.ticker for e in ems] == ["BIG"]


class _FakeFiling:
    def __init__(self, acc):
        self.accession_no = acc


def test_dedup_by_accession_keeps_first():
    # get_filings returns every row 2x (verified) -> dedup before any header fetch.
    fs = [_FakeFiling("a1"), _FakeFiling("a1"), _FakeFiling("a2"), _FakeFiling("a2")]
    out = _dedup_by_accession(fs)
    assert [f.accession_no for f in out] == ["a1", "a2"]


def test_walkback_to_last_published():
    calls = []

    def fake(d, cap, ident, resolve):
        calls.append(d)
        return [_rec("X", "A", acc="a")] if d == date(2026, 6, 17) else []

    recs, used = fetch_recent_activist_records(date(2026, 6, 18), 300, "id",
                                               lambda c: "X", _fetch=fake)
    assert used == date(2026, 6, 17) and recs
    assert calls[0] == date(2026, 6, 18)   # tries the session first
