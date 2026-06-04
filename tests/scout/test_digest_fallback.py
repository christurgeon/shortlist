import json

from shortlist.scout.daily import _one_line_brief_from_file


def _write(tmp_path, data) -> str:
    md = tmp_path / "AAPL" / "acc.md"
    md.parent.mkdir(parents=True, exist_ok=True)
    md.write_text("# brief")
    (tmp_path / "AAPL" / "acc.json").write_text(json.dumps(data))
    return str(md)


def test_reads_injected_synthesis(tmp_path):
    p = _write(tmp_path, {"synthesis": "Injected line.",
                          "thesis": {"takeaway": "Injected line."}})
    assert _one_line_brief_from_file(p) == "Injected line."


def test_falls_back_to_thesis_takeaway(tmp_path):
    p = _write(tmp_path, {"thesis": {"takeaway": "From thesis."}})  # no top-level synthesis
    assert _one_line_brief_from_file(p) == "From thesis."


def test_old_record_still_reads(tmp_path):
    p = _write(tmp_path, {"synthesis": "Legacy line."})             # pre-change record
    assert _one_line_brief_from_file(p) == "Legacy line."
