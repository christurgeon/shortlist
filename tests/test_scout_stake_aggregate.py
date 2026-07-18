"""Pure aggregator: 13D/A amendment records (+ baselines) -> (emissions, baseline_updates)."""
from shortlist.scout.edgar_index import stake_increases_from_records


def _rec(**kw):
    base = {"ticker": "TGT", "cik": "0000000123", "filer_cik": "0000000900",
            "subject_name": "Target Co", "activist": "Fund LP",
            "form": "SCHEDULE 13D/A", "accession": "a1",
            "file_date": "2026-07-10", "stake_pct": 8.0}
    return {**base, **kw}


def test_material_increase_emits_with_meta():
    ems, upd = stake_increases_from_records(
        [_rec()], {"0000000900|0000000123": {"pct": 5.5, "date": "2026-01-01"}})
    assert len(ems) == 1
    e = ems[0]
    assert e.signal == "edgar:13d_stake_increase" and e.ticker == "TGT"
    assert e.cik == "0000000123" and e.is_discovery
    assert e.meta == {"adsh": "a1", "prior_pct": 5.5, "new_pct": 8.0,
                      "file_date": "2026-07-10"}
    assert upd["0000000900|0000000123"] == {"pct": 8.0, "date": "2026-07-10", "adsh": "a1"}


def test_immaterial_increase_updates_baseline_but_never_emits():
    ems, upd = stake_increases_from_records(
        [_rec(stake_pct=6.9)], {"0000000900|0000000123": {"pct": 5.5, "date": "2026-01-01"}})
    assert ems == [] and upd["0000000900|0000000123"]["pct"] == 6.9


def test_decrease_never_emits():
    ems, _ = stake_increases_from_records(
        [_rec(stake_pct=3.0)], {"0000000900|0000000123": {"pct": 5.5, "date": "2026-01-01"}})
    assert ems == []


def test_no_baseline_seeds_only():
    ems, upd = stake_increases_from_records([_rec()], {})
    assert ems == [] and upd["0000000900|0000000123"]["pct"] == 8.0


def test_unparsed_stake_neither_emits_nor_updates():
    ems, upd = stake_increases_from_records(
        [_rec(stake_pct=None)], {"0000000900|0000000123": {"pct": 5.5, "date": "2026-01-01"}})
    assert ems == [] and upd == {}


def test_quality_drops_apply():
    spac = _rec(subject_name="Blank Check Acquisition Corp")
    aff = _rec(activist="Target Holdings LLC")          # distinctive-token overlap
    ems, upd = stake_increases_from_records(
        [spac, aff], {"0000000900|0000000123": {"pct": 5.5, "date": "2026-01-01"}})
    assert ems == [] and upd == {}                       # excluded by signal definition


def test_missing_filer_cik_skipped():
    ems, upd = stake_increases_from_records(
        [_rec(filer_cik=None)], {})
    assert ems == [] and upd == {}
