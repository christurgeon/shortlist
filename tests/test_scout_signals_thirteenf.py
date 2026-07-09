"""13F marquee-fund cloning originator (scout/thirteenf.py + EdgarThirteenFSignal): infotable
parse, putCall + PRN drops, same-CUSIP multi-row aggregation, new-position diff + strength
math, top_n, submissions selection with /A exclusion, empty-diff-still-processed, the
max_filings_per_day carry-over, and error degrade + secret redaction. All offline."""
from datetime import date

from shortlist.scout.signals import EdgarThirteenFSignal, build_signals
from shortlist.scout.thirteenf import (
    aggregate_positions,
    new_position_diff,
    parse_infotable,
    parse_submissions_13fhr,
    thirteenf_emissions,
)

_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def _infotable(rows) -> str:
    """rows: list of (name, cusip, value, ssh_type, put_call). put_call '' = none."""
    body = []
    for name, cusip, value, ssh, pc in rows:
        pc_tag = f"<putCall>{pc}</putCall>" if pc else ""
        body.append(
            f"<infoTable><nameOfIssuer>{name}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
            f"<cusip>{cusip}</cusip><value>{value}</value>{pc_tag}"
            f"<shrsOrPrnAmt><sshPrnamt>1</sshPrnamt><sshPrnamtType>{ssh}</sshPrnamtType></shrsOrPrnAmt>"
            f"</infoTable>")
    return f'<informationTable xmlns="{_NS}">{"".join(body)}</informationTable>'


def _submissions(forms):
    """forms: list of (form, accession, filing_date, report_date) newest-first."""
    return {"filings": {"recent": {
        "form": [f[0] for f in forms],
        "accessionNumber": [f[1] for f in forms],
        "filingDate": [f[2] for f in forms],
        "reportDate": [f[3] for f in forms]}}}


# --- pure parse / aggregate / diff -----------------------------------------------------

def test_parse_infotable_namespace_agnostic():
    rows = parse_infotable(_infotable([("ALLY FINL INC", "02005N100", 100, "SH", "")]))
    assert rows == [{"cusip": "02005N100", "name": "ALLY FINL INC", "title": "COM",
                     "value": 100.0, "put_call": "", "ssh_type": "SH"}]


def test_aggregate_sums_same_cusip_multirow_and_drops_options_and_prn():
    xml = _infotable([
        ("ALLY", "02005N100", 100, "SH", ""),     # voting-split row 1
        ("ALLY", "02005N100", 50, "SH", ""),      # voting-split row 2 -> summed to 150
        ("ALLY", "02005N100", 999, "SH", "Call"), # option -> dropped
        ("BOND", "11111X111", 500, "PRN", ""),    # convertible debt -> dropped
    ])
    agg = aggregate_positions(parse_infotable(xml))
    assert set(agg) == {"02005N100"}               # only the equity CUSIP survives
    assert agg["02005N100"]["value"] == 150.0      # sole+shared summed


def test_new_position_diff_weight_and_strength_math():
    latest = {"NEWCUS": {"value": 50.0, "name": "New Co", "title": "COM"},
              "OLDCUS": {"value": 950.0, "name": "Old Co", "title": "COM"}}
    prior = {"OLDCUS": {"value": 900.0, "name": "Old Co", "title": "COM"}}
    out = new_position_diff(latest, prior, min_position_pct=0.005, full_strength_pct=0.05)
    assert [d["cusip"] for d in out] == ["NEWCUS"]  # only the genuinely-new CUSIP
    d = out[0]
    assert abs(d["weight"] - 0.05) < 1e-9           # 50 / 1000
    # design §1.8: strength = min(1.0, weight / full_strength_pct); weight==full_strength -> 1.0
    assert abs(d["strength"] - 1.0) < 1e-9


def test_new_position_diff_strength_is_linear_not_reshaped():
    """A barely-qualifying 0.5%-of-book position gets a PROPORTIONALLY small strength
    (0.005/0.05 = 0.1), not the old base+conv inflation (~0.485); a full 5% bet -> 1.0."""
    small = {"S": {"value": 5.0, "name": "S", "title": "C"},
             "REST": {"value": 995.0, "name": "R", "title": "C"}}
    out = new_position_diff(small, {"REST": {"value": 995.0, "name": "R", "title": "C"}},
                            min_position_pct=0.005, full_strength_pct=0.05)
    assert abs(out[0]["weight"] - 0.005) < 1e-9
    assert abs(out[0]["strength"] - 0.1) < 1e-9     # min(1.0, 0.005/0.05), no 0.45 base
    # a 5% bet caps at full conviction 1.0 (not the old 0.80 cap)
    big = {"B": {"value": 50.0, "name": "B", "title": "C"},
           "REST": {"value": 950.0, "name": "R", "title": "C"}}
    out2 = new_position_diff(big, {"REST": {"value": 950.0, "name": "R", "title": "C"}},
                             min_position_pct=0.005, full_strength_pct=0.05)
    assert abs(out2[0]["strength"] - 1.0) < 1e-9


def test_new_position_diff_min_threshold_and_ordering():
    latest = {"A": {"value": 4.0, "name": "A", "title": "C"},    # 0.4% -> below 0.5% floor
              "B": {"value": 300.0, "name": "B", "title": "C"},
              "C": {"value": 696.0, "name": "C", "title": "C"}}
    out = new_position_diff(latest, {}, min_position_pct=0.005, full_strength_pct=0.05)
    assert [d["cusip"] for d in out] == ["C", "B"]  # weight-desc, sub-floor A dropped


def test_thirteenf_emissions_top_n_and_abstention_and_junk_drop():
    positions = [
        {"cusip": "C1", "name": "One", "title": "COM", "value": 1, "weight": 0.10, "strength": 0.8},
        {"cusip": "C2", "name": "Two", "title": "COM", "value": 1, "weight": 0.08, "strength": 0.7},
        {"cusip": "C3", "name": "Unresolvable", "title": "COM", "value": 1, "weight": 0.06, "strength": 0.6},
        {"cusip": "C4", "name": "Junk", "title": "COM", "value": 1, "weight": 0.05, "strength": 0.5},
    ]
    resolved = {"C1": "AAA", "C2": "BBB", "C3": None, "C4": "ABCDY"}  # C4 -> 5th-letter junk

    def resolve(cusip, name):
        return resolved.get(cusip)

    ems, abst = thirteenf_emissions(positions, resolve_fn=resolve, fund_name="Fund X",
                                    period="2026-03-31", filing_date="2026-05-15",
                                    deny_list=[], top_n=10)
    assert [e.ticker for e in ems] == ["AAA", "BBB"]  # C3 abstains, C4 is 5th-letter junk
    assert abst == 1                                 # C3 abstained
    assert ems[0].signal == "edgar:13f_new_position"
    assert ems[0].cik is None                        # CUSIP resolver yields no CIK (stated limit)
    assert "Fund X new 13F position (Q1 2026, filed 2026-05-15): 10.0% of book" == ems[0].evidence
    # top_n caps KEPT names; an unresolved position never consumes a slot (break before C3).
    ems1, abst1 = thirteenf_emissions(positions, resolve_fn=resolve, fund_name="Fund X",
                                      period="2026-03-31", filing_date="2026-05-15", top_n=1)
    assert [e.ticker for e in ems1] == ["AAA"] and abst1 == 0


def test_thirteenf_emissions_meta_carries_fund_identity_and_accession():
    """meta must carry structured fund identity + filing accession (firehose join keys) —
    parity with the 8-K/13D/buyback emissions, not just the free-text evidence string."""
    positions = [{"cusip": "C1", "name": "One", "title": "COM", "value": 1,
                  "weight": 0.10, "strength": 0.8}]
    ems, _ = thirteenf_emissions(positions, resolve_fn=lambda c, n: "AAA",
                                 fund_name="Berkshire Hathaway", period="2026-03-31",
                                 filing_date="2026-05-15", fund_cik=1067983,
                                 accession="0001067983-26-000012")
    m = ems[0].meta
    assert m["fund_cik"] == "1067983"                # stringified for a stable join key
    assert m["fund_name"] == "Berkshire Hathaway"
    assert m["adsh"] == "0001067983-26-000012"
    assert m["cusip"] == "C1" and m["weight"] == 0.10  # existing keys preserved


def test_parse_submissions_excludes_amendments_newest_first():
    subm = _submissions([
        ("13F-HR/A", "acc-A", "2026-05-16", "2026-03-31"),  # amendment -> excluded
        ("13F-HR", "acc-1", "2026-05-15", "2026-03-31"),
        ("4", "acc-x", "2026-05-10", ""),
        ("13F-HR", "acc-0", "2026-02-14", "2025-12-31")])
    got = parse_submissions_13fhr(subm)
    assert [f["accession"] for f in got] == ["acc-1", "acc-0"]


# --- signal-level orchestration --------------------------------------------------------

def test_registered_and_named():
    sig = build_signals(["edgar_13f"], {"edgar_13f": {"funds": []}})[0]
    assert isinstance(sig, EdgarThirteenFSignal)
    assert sig.name == "edgar_13f" and sig.is_discovery is True


class _FakeResolver:
    def __init__(self, mapping):
        self.mapping = mapping

    def resolve(self, cusip, name):
        return self.mapping.get(cusip)


def _wire(sig, monkeypatch, *, submissions, infotables):
    """Inject a fake resolver + throttle and monkeypatch the thirteenf fetch seams.
    submissions: {cik -> submissions-json}; infotables: {accession -> xml str}."""
    import shortlist.scout.thirteenf as tf
    sig._throttle = lambda: None

    def fake_subm(cik, identity, **kw):
        return submissions[int(cik)]

    def fake_info(cik, accession, identity, **kw):
        return parse_infotable(infotables[accession])

    monkeypatch.setattr(tf, "fetch_submissions", fake_subm)
    monkeypatch.setattr(tf, "fetch_infotable_rows", fake_info)


def test_scan_emits_new_position_and_marks_processed(monkeypatch):
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}])
    sig._resolver = _FakeResolver({"NEWCUS": "NEW", "OLDCUS": "OLD"})
    _wire(sig, monkeypatch,
          submissions={1067983: _submissions([
              ("13F-HR", "acc-latest", "2026-05-15", "2026-03-31"),
              ("13F-HR", "acc-prior", "2026-02-14", "2025-12-31")])},
          infotables={
              "acc-latest": _infotable([("New Co", "NEWCUS", 50, "SH", ""),
                                        ("Old Co", "OLDCUS", 950, "SH", "")]),
              "acc-prior": _infotable([("Old Co", "OLDCUS", 900, "SH", "")])})
    ems = sig.scan(date(2026, 5, 20))
    assert [e.ticker for e in ems] == ["NEW"]
    assert sig.processed_accessions == ["acc-latest"]   # latest marked processed


def test_scan_empty_diff_still_marks_processed(monkeypatch):
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}])
    sig._resolver = _FakeResolver({"OLDCUS": "OLD"})
    _wire(sig, monkeypatch,
          submissions={1067983: _submissions([
              ("13F-HR", "acc-latest", "2026-05-15", "2026-03-31"),
              ("13F-HR", "acc-prior", "2026-02-14", "2025-12-31")])},
          infotables={"acc-latest": _infotable([("Old Co", "OLDCUS", 1000, "SH", "")]),
                      "acc-prior": _infotable([("Old Co", "OLDCUS", 900, "SH", "")])})
    ems = sig.scan(date(2026, 5, 20))
    assert ems == []                                     # no new positions
    assert sig.processed_accessions == ["acc-latest"]    # STILL processed (no daily re-fetch)


def test_scan_skips_already_seen_accession(monkeypatch):
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}],
                               seen_accessions=["acc-latest"])
    sig._resolver = _FakeResolver({})
    calls = []
    import shortlist.scout.thirteenf as tf
    sig._throttle = lambda: None
    monkeypatch.setattr(tf, "fetch_submissions",
                        lambda cik, identity, **kw: _submissions([
                            ("13F-HR", "acc-latest", "2026-05-15", "2026-03-31"),
                            ("13F-HR", "acc-prior", "2026-02-14", "2025-12-31")]))
    monkeypatch.setattr(tf, "fetch_infotable_rows",
                        lambda *a, **k: calls.append(1) or [])
    ems = sig.scan(date(2026, 5, 20))
    assert ems == [] and sig.processed_accessions == []
    assert calls == []                                   # zero infotable fetches when seen


def test_scan_max_filings_carry_over(monkeypatch):
    funds = [{"cik": i, "name": f"F{i}"} for i in (1, 2, 3)]
    sig = EdgarThirteenFSignal(funds=funds, max_filings_per_day=2)
    sig._resolver = _FakeResolver({"NEWCUS": "NEW", "OLDCUS": "OLD"})
    subs = {i: _submissions([("13F-HR", f"acc-{i}-latest", "2026-05-15", "2026-03-31"),
                             ("13F-HR", f"acc-{i}-prior", "2026-02-14", "2025-12-31")])
            for i in (1, 2, 3)}
    infos = {}
    for i in (1, 2, 3):
        infos[f"acc-{i}-latest"] = _infotable([("New", "NEWCUS", 50, "SH", ""),
                                               ("Old", "OLDCUS", 950, "SH", "")])
        infos[f"acc-{i}-prior"] = _infotable([("Old", "OLDCUS", 900, "SH", "")])
    _wire(sig, monkeypatch, submissions=subs, infotables=infos)
    sig.scan(date(2026, 5, 20))
    assert sig.processed_accessions == ["acc-1-latest", "acc-2-latest"]  # 3rd carried over
    ran, detail = sig.available()
    assert "carry-over" in detail

    # Next session: the two processed are seen; the 3rd fund is now picked up.
    sig2 = EdgarThirteenFSignal(funds=funds, max_filings_per_day=2,
                                seen_accessions=["acc-1-latest", "acc-2-latest"])
    sig2._resolver = _FakeResolver({"NEWCUS": "NEW", "OLDCUS": "OLD"})
    _wire(sig2, monkeypatch, submissions=subs, infotables=infos)
    sig2.scan(date(2026, 5, 21))
    assert sig2.processed_accessions == ["acc-3-latest"]


def test_scan_empty_infotable_retry_heals_within_scan(monkeypatch):
    """An empty infotable parse retries ONCE in-scan (EDGAR is uncached, so a transient
    truncation heals) — a heal on the retry yields the normal diff, no lost quarter."""
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}])
    sig._resolver = _FakeResolver({"NEWCUS": "NEW", "OLDCUS": "OLD"})
    import shortlist.scout.thirteenf as tf
    sig._throttle = lambda: None
    subs = _submissions([("13F-HR", "acc-latest", "2026-05-15", "2026-03-31"),
                         ("13F-HR", "acc-prior", "2026-02-14", "2025-12-31")])
    infos = {"acc-latest": _infotable([("New Co", "NEWCUS", 50, "SH", ""),
                                       ("Old Co", "OLDCUS", 950, "SH", "")]),
             "acc-prior": _infotable([("Old Co", "OLDCUS", 900, "SH", "")])}
    calls: dict[str, int] = {}

    def fake_info(cik, accession, identity, **kw):
        calls[accession] = calls.get(accession, 0) + 1
        if accession == "acc-prior" and calls[accession] == 1:
            return []                                    # transient empty; heals on the retry
        return parse_infotable(infos[accession])

    monkeypatch.setattr(tf, "fetch_submissions", lambda cik, identity, **kw: subs)
    monkeypatch.setattr(tf, "fetch_infotable_rows", fake_info)
    ems = sig.scan(date(2026, 5, 20))
    assert [e.ticker for e in ems] == ["NEW"]            # healed -> normal diff
    assert calls["acc-prior"] == 2                       # retried exactly once
    assert sig.processed_accessions == ["acc-latest"]
    assert sig.available()[0] is True


def test_scan_empty_infotable_after_retry_marks_processed_loudly(monkeypatch):
    """A prior infotable that parses EMPTY on BOTH the initial fetch AND the retry must NOT
    emit the whole current book as 'new'; it counts a fund error, emits nothing, but MARKS
    the accession processed (no daily-refetch wedge) with a LOUD named note. Trade-off: a
    truly-transient empty loses one quarter — accepted over the old retry-forever wedge."""
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}])
    sig._resolver = _FakeResolver({"NEWCUS": "NEW", "OLDCUS": "OLD"})
    import shortlist.scout.thirteenf as tf
    sig._throttle = lambda: None
    subs = _submissions([("13F-HR", "acc-latest", "2026-05-15", "2026-03-31"),
                         ("13F-HR", "acc-prior", "2026-02-14", "2025-12-31")])
    calls: dict[str, int] = {}

    def fake_info(cik, accession, identity, **kw):
        calls[accession] = calls.get(accession, 0) + 1
        if accession == "acc-latest":
            return parse_infotable(_infotable([("New Co", "NEWCUS", 50, "SH", ""),
                                               ("Old Co", "OLDCUS", 950, "SH", "")]))
        return []                                         # prior ALWAYS empty (retry too)

    monkeypatch.setattr(tf, "fetch_submissions", lambda cik, identity, **kw: subs)
    monkeypatch.setattr(tf, "fetch_infotable_rows", fake_info)
    ems = sig.scan(date(2026, 5, 20))
    assert ems == []                                     # NOT the whole current book
    assert sig.processed_accessions == ["acc-latest"]    # MARKED — no daily-refetch wedge
    assert calls["acc-prior"] == 2                        # retried once before giving up
    ran, detail = sig.available()
    assert ran is False and "1 fund errors" in detail
    assert "empty infotable parse — quarter skipped" in detail and "acc-prior" in detail


def test_scan_empty_ftd_index_skips_without_marking_and_resets(monkeypatch):
    """An empty FTD CUSIP index (FTD outage / datacenter-IP block) must abort the whole scan
    (the exact-name fallback alone is too weak to justify burning quarters) rather than
    abstain-then-mark every quarter — AND drop the memoized resolver so the retry is real."""
    from shortlist.scout.cusip_map import CusipResolver
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}])
    sig._resolver = CusipResolver({}, {})                # zero FTD + zero name entries
    calls = []
    import shortlist.scout.thirteenf as tf
    sig._throttle = lambda: None
    monkeypatch.setattr(tf, "fetch_submissions",
                        lambda *a, **k: calls.append(1) or _submissions([]))
    ems = sig.scan(date(2026, 5, 20))
    assert ems == []
    assert sig.processed_accessions == []                # nothing marked
    assert calls == []                                   # bailed before any fund fetch
    assert sig._resolver is None                         # memoized resolver dropped (real retry)
    ran, detail = sig.available()
    assert ran is False and "FTD index empty" in detail


def test_scan_ftd_only_outage_still_aborts(monkeypatch):
    """An FTD-only outage (empty FTD index but a NON-empty name index — the likely
    datacenter-IP-block case) must STILL abort: the exact-match name fallback alone can't
    justify marking quarters processed."""
    from shortlist.scout.cusip_map import CusipResolver
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}])
    sig._resolver = CusipResolver({}, {"BERKSHIRE HATHAWAY": "BRK-A"})   # FTD empty, name OK
    calls = []
    import shortlist.scout.thirteenf as tf
    sig._throttle = lambda: None
    monkeypatch.setattr(tf, "fetch_submissions",
                        lambda *a, **k: calls.append(1) or _submissions([]))
    ems = sig.scan(date(2026, 5, 20))
    assert ems == [] and sig.processed_accessions == [] and calls == []
    assert sig._resolver is None
    assert sig.available()[0] is False


def test_scan_degrades_on_error_and_redacts(monkeypatch):
    sig = EdgarThirteenFSignal(funds=[{"cik": 1, "name": "F"}])
    sig._resolver = _FakeResolver({})
    sig._throttle = lambda: None
    import shortlist.scout.thirteenf as tf

    def boom(*a, **k):
        raise RuntimeError("SEC 500 https://data.sec.gov/x?token=SECRET")

    monkeypatch.setattr(tf, "fetch_submissions", boom)
    ems = sig.scan(date(2026, 5, 20))
    assert ems == []
    ran, detail = sig.available()
    assert ran is False and "SECRET" not in detail       # per-fund error isolated + redacted
