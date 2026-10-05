import json
import zipfile

import pytest

from shortlist.backtest.durability_data import (
    _cik_of,
    compact_companyfacts_zip,
    compact_facts,
    iter_compacted,
    require_zip,
    sic_from_submissions_zip,
)
from shortlist.durability import snapshot


def _raw(forms=("10-K",), tag="OperatingIncomeLoss"):
    rows = [{"start": "2015-01-01", "end": "2015-12-31", "val": 100.0, "filed": "2016-02-20",
             "form": f, "accn": "0000000000-16-000001", "fy": 2015, "fp": "FY", "frame": "CY2015"}
            for f in forms]
    return {"cik": 320193, "entityName": "TEST CO",
            "facts": {"us-gaap": {tag: {"units": {"USD": rows}},
                                  "Goodwill": {"units": {"USD": rows}}}}}


def test_compact_keeps_only_needed_tags_fields_and_10k_forms():
    out = compact_facts(_raw(forms=("10-K", "10-Q", "20-F", "10-K/A")))
    rows = out["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"]
    assert [r["form"] for r in rows] == ["10-K", "10-K/A"]
    assert set(rows[0]) == {"start", "end", "val", "filed", "form"}
    assert "Goodwill" not in out["facts"]["us-gaap"]


def test_compact_drops_a_filer_with_no_annual_revenue_or_operating_income():
    assert compact_facts(_raw(forms=("10-Q",))) is None
    assert compact_facts(_raw(tag="Assets")) is None
    assert compact_facts({"facts": {}}) is None
    assert compact_facts({}) is None


def test_cik_of_rejects_paging_and_foreign_names():
    assert _cik_of("CIK0000320193.json") == "0000320193"
    assert _cik_of("CIK0000320193-submissions-001.json") is None
    assert _cik_of("placeholder.txt") is None
    assert _cik_of("CIK123.json") is None


def test_zip_roundtrip_reads_back_through_the_real_extractor(tmp_path):
    zp, out = tmp_path / "cf.zip", tmp_path / "cf.jsonl.gz"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000320193.json", json.dumps(_raw()))
        z.writestr("CIK0000000002.json", json.dumps(_raw(forms=("10-Q",))))   # dropped
        z.writestr("CIK0000000003.json", "{not json")                          # skipped
    assert compact_companyfacts_zip(zp, out) == 1
    (rec,) = list(iter_compacted(out))
    assert rec["cik"] == "0000320193" and rec["name"] == "TEST CO"
    # the compacted shape is what annual_series expects: the real snapshot reads it
    assert snapshot(rec, 2015)[2015].op_income == 100.0


def test_sic_lookup_reads_only_wanted_main_files(tmp_path):
    zp = tmp_path / "sub.zip"
    with zipfile.ZipFile(zp, "w") as z:
        z.writestr("CIK0000320193.json", json.dumps({"sic": "3571"}))
        z.writestr("CIK0000320193-submissions-001.json", json.dumps({"sic": "9999"}))
        z.writestr("CIK0000000002.json", json.dumps({"sic": "6021"}))
        z.writestr("CIK0000000004.json", json.dumps({"sic": ""}))
        z.writestr("CIK0000000005.json", "[]")
    got = sic_from_submissions_zip(zp, {"0000320193", "0000000004", "0000000005"})
    assert got == {"0000320193": "3571"}


def test_require_zip_deletes_a_block_page_and_keeps_a_real_archive(tmp_path):
    html = tmp_path / "companyfacts.zip.tmp"
    html.write_text("<html>Your Request Originates from an Undeclared Automated Tool</html>")
    with pytest.raises(ValueError, match="not a ZIP"):
        require_zip(html)
    assert not html.exists()
    good = tmp_path / "ok.zip"
    with zipfile.ZipFile(good, "w") as z:
        z.writestr("a.txt", "x")
    require_zip(good)
    assert good.exists()
