"""13F marquee-fund cloning originator (scout/thirteenf.py + EdgarThirteenFSignal): infotable
parse, putCall + PRN drops, same-CUSIP multi-row aggregation, new-position diff + strength
math, top_n, submissions selection with /A exclusion, empty-diff-still-processed, the
max_filings_per_day carry-over, and error degrade + secret redaction. All offline."""
from datetime import date

from shortlist.scout.signals import EdgarThirteenFSignal, build_signals
from shortlist.scout.thirteenf import (
    aggregate_positions,
    material_add_diff,
    new_position_diff,
    parse_infotable,
    parse_submissions_13fhr,
    thirteenf_emissions,
)

_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"


def _infotable(rows) -> str:
    """rows: list of (name, cusip, value, ssh_type, put_call[, shares]). put_call '' = none.
    `shares` (sshPrnamt) defaults to 1, so existing 5-tuple call sites are unchanged."""
    body = []
    for row in rows:
        name, cusip, value, ssh, pc = row[:5]
        shares = row[5] if len(row) > 5 else 1
        pc_tag = f"<putCall>{pc}</putCall>" if pc else ""
        body.append(
            f"<infoTable><nameOfIssuer>{name}</nameOfIssuer><titleOfClass>COM</titleOfClass>"
            f"<cusip>{cusip}</cusip><value>{value}</value>{pc_tag}"
            f"<shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt>"
            f"<sshPrnamtType>{ssh}</sshPrnamtType></shrsOrPrnAmt>"
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
                     "value": 100.0, "put_call": "", "ssh_type": "SH", "shares": 1.0}]


def test_parse_infotable_shares_comma_formatted():
    rows = parse_infotable(_infotable([("ALLY", "02005N100", 100, "SH", "", "1,234,567")]))
    assert rows[0]["shares"] == 1234567.0


def test_parse_infotable_shares_non_numeric_is_none():
    rows = parse_infotable(_infotable([("ALLY", "02005N100", 100, "SH", "", "n/a")]))
    assert rows[0]["shares"] is None


def test_parse_infotable_missing_sshprnamt_is_none():
    """`sshPrnamt` absent entirely -> None, never 0.0 (which would read as "holds nothing")."""
    xml = (f'<informationTable xmlns="{_NS}"><infoTable>'
           "<nameOfIssuer>ALLY</nameOfIssuer><titleOfClass>COM</titleOfClass>"
           "<cusip>02005N100</cusip><value>100</value>"
           "<shrsOrPrnAmt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
           "</infoTable></informationTable>")
    assert parse_infotable(xml)[0]["shares"] is None


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


def test_aggregate_sums_shares_across_voting_split_rows():
    xml = _infotable([
        ("ALLY", "02005N100", 100, "SH", "", 60),   # sole voting
        ("ALLY", "02005N100", 50, "SH", "", 40),    # shared voting -> 100 shares total
    ])
    agg = aggregate_positions(parse_infotable(xml))
    assert agg["02005N100"]["shares"] == 100.0
    assert agg["02005N100"]["value"] == 150.0       # value unchanged by this task


def test_aggregate_shares_all_none_stays_none_not_zero():
    """A missing share count must ABSTAIN downstream, never read as zero shares held."""
    xml = (f'<informationTable xmlns="{_NS}"><infoTable>'
           "<nameOfIssuer>ALLY</nameOfIssuer><titleOfClass>COM</titleOfClass>"
           "<cusip>02005N100</cusip><value>100</value>"
           "<shrsOrPrnAmt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
           "</infoTable></informationTable>")
    agg = aggregate_positions(parse_infotable(xml))
    assert agg["02005N100"]["shares"] is None


def test_aggregate_shares_mixed_none_sums_only_the_present():
    xml = _infotable([
        ("ALLY", "02005N100", 100, "SH", "", 60),
        ("ALLY", "02005N100", 50, "SH", "", "n/a"),   # unparseable -> contributes nothing
    ])
    agg = aggregate_positions(parse_infotable(xml))
    assert agg["02005N100"]["shares"] == 60.0


def test_aggregate_shares_none_first_then_present_still_sums():
    """Order must not matter: a None-shares row seen FIRST must not swallow a later count."""
    xml = _infotable([
        ("ALLY", "02005N100", 50, "SH", "", "n/a"),
        ("ALLY", "02005N100", 100, "SH", "", 60),
    ])
    agg = aggregate_positions(parse_infotable(xml))
    assert agg["02005N100"]["shares"] == 60.0


def test_aggregate_option_and_prn_rows_do_not_contribute_shares():
    xml = _infotable([
        ("ALLY", "02005N100", 100, "SH", "", 60),
        ("ALLY", "02005N100", 999, "SH", "Call", 500),   # option -> dropped entirely
        ("ALLY", "02005N100", 999, "PRN", "", 700),      # convertible debt -> dropped
    ])
    agg = aggregate_positions(parse_infotable(xml))
    assert agg["02005N100"]["shares"] == 60.0
    assert agg["02005N100"]["value"] == 100.0


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
    assert ems[0].evidence == "Fund X new 13F position (Q1 2026, filed 2026-05-15): 10.0% of book"
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


# --- material-add diff -----------------------------------------------------------------

def _book(entries):
    """entries: {cusip: (value, shares)} -> an aggregate_positions-shaped dict."""
    return {c: {"value": float(v), "shares": (None if s is None else float(s)),
                "name": c, "title": "COM"} for c, (v, s) in entries.items()}


def test_material_add_fires_on_share_growth_over_ratio():
    latest = _book({"AAA": (300, 150), "BBB": (700, 10)})   # AAA shares 100 -> 150 = 1.5x
    prior = _book({"AAA": (200, 100), "BBB": (800, 10)})
    adds, abst = material_add_diff(latest, prior)
    assert [a["cusip"] for a in adds] == ["AAA"]
    assert adds[0]["share_ratio"] == 1.5
    assert abst == 0


def test_material_add_ignores_price_only_weight_growth():
    """THE core guard: value up 3x, shares flat -> not an add. Price is not conviction."""
    latest = _book({"AAA": (900, 100), "BBB": (100, 10)})
    prior = _book({"AAA": (300, 100), "BBB": (700, 10)})
    adds, _ = material_add_diff(latest, prior)
    assert adds == []


def test_material_add_below_ratio_does_not_fire():
    latest = _book({"AAA": (500, 140), "BBB": (500, 10)})    # 1.4x < 1.5x
    prior = _book({"AAA": (500, 100), "BBB": (500, 10)})
    adds, _ = material_add_diff(latest, prior)
    assert adds == []


def test_material_add_below_weight_floor_does_not_fire():
    """Shares tripled, but the position is 0.1% of the book -- below min_position_pct."""
    latest = _book({"AAA": (1, 300), "BBB": (999, 10)})
    prior = _book({"AAA": (1, 100), "BBB": (999, 10)})
    adds, _ = material_add_diff(latest, prior)
    assert adds == []


def test_material_add_excludes_new_positions():
    """A CUSIP absent from prior is a NEW position, never an add -- no cohort overlap."""
    latest = _book({"AAA": (500, 100), "BBB": (500, 10)})
    prior = _book({"BBB": (500, 10)})
    adds, _ = material_add_diff(latest, prior)
    assert adds == []


def test_material_add_excludes_exits():
    latest = _book({"BBB": (500, 10)})
    prior = _book({"AAA": (500, 100), "BBB": (500, 10)})
    adds, _ = material_add_diff(latest, prior)
    assert adds == []


def test_material_add_abstains_on_missing_shares_either_side():
    latest = _book({"AAA": (500, None), "BBB": (500, 10)})
    prior = _book({"AAA": (500, 100), "BBB": (500, 10)})
    adds, abst = material_add_diff(latest, prior)
    assert adds == [] and abst == 1

    latest2 = _book({"AAA": (500, 300), "BBB": (500, 10)})
    prior2 = _book({"AAA": (500, None), "BBB": (500, 10)})
    adds2, abst2 = material_add_diff(latest2, prior2)
    assert adds2 == [] and abst2 == 1


def test_material_add_abstains_on_zero_prior_shares():
    """0 -> 200 is an infinite ratio, not a measurable add. Abstain, never guess."""
    latest = _book({"AAA": (500, 200), "BBB": (500, 10)})
    prior = _book({"AAA": (500, 0), "BBB": (500, 10)})
    adds, abst = material_add_diff(latest, prior)
    assert adds == [] and abst == 1


def test_material_add_strength_scales_on_weight_increment():
    """delta_weight 0.05 at full_strength_pct 0.05 -> full strength.

    Tolerance, not equality: 0.15 - 0.10 is 0.04999999999999999 in binary float.
    """
    latest = _book({"AAA": (150, 200), "BBB": (850, 10)})    # w 0.15
    prior = _book({"AAA": (100, 100), "BBB": (900, 10)})     # w 0.10 -> delta 0.05
    adds, _ = material_add_diff(latest, prior)
    assert abs(adds[0]["delta_weight"] - 0.05) < 1e-9
    assert abs(adds[0]["strength"] - 1.0) < 1e-9


def test_material_add_negative_delta_weight_clamps_to_zero_strength():
    """Shares bought into a price decline: still an add, at floor strength, never negative."""
    latest = _book({"AAA": (100, 300), "BBB": (900, 10)})    # w 0.10
    prior = _book({"AAA": (300, 100), "BBB": (700, 10)})     # w 0.30 -> delta -0.20
    adds, _ = material_add_diff(latest, prior)
    assert [a["cusip"] for a in adds] == ["AAA"]
    assert adds[0]["strength"] == 0.0


def test_material_add_empty_book_returns_empty():
    assert material_add_diff({}, _book({"AAA": (1, 1)})) == ([], 0)
    assert material_add_diff(_book({"AAA": (0, 1)}), _book({"AAA": (1, 1)})) == ([], 0)
    assert material_add_diff(_book({"AAA": (1, 1)}), {}) == ([], 0)


def test_material_add_ordering_is_deterministic():
    latest = _book({"AAA": (300, 300), "BBB": (300, 200), "CCC": (400, 10)})
    prior = _book({"AAA": (300, 100), "BBB": (300, 100), "CCC": (400, 10)})
    adds, _ = material_add_diff(latest, prior)
    assert [a["cusip"] for a in adds] == ["AAA", "BBB"]   # ratio 3.0 then 2.0


def test_material_add_and_new_position_diffs_are_disjoint():
    """Cohort-contamination guard: no CUSIP may appear in both result sets."""
    latest = _book({"AAA": (300, 150), "NEW": (300, 50), "CCC": (400, 10)})
    prior = _book({"AAA": (200, 100), "CCC": (400, 10)})
    news = new_position_diff(latest, prior)
    adds, _ = material_add_diff(latest, prior)
    assert {a["cusip"] for a in adds}.isdisjoint({n["cusip"] for n in news})
    assert {n["cusip"] for n in news} == {"NEW"}
    assert {a["cusip"] for a in adds} == {"AAA"}


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


_ADD_CUSIPS = {"OLDCUS": "OLDTKR", "NEWCUS": "NEWTKR", "BIGCUS": "BIGTKR",
               "ACUS": "ATKR", "BCUS": "BTKR", "CCUS": "CTKR"}


def _signal_with(monkeypatch, infos, *, material_add, top_n=10):
    """An EdgarThirteenFSignal wired to two fake infotables ('acc-latest'/'acc-prior')."""
    sig = EdgarThirteenFSignal(funds=[{"cik": 1067983, "name": "Berkshire"}],
                               top_n=top_n, material_add=material_add)
    sig._resolver = _FakeResolver(dict(_ADD_CUSIPS))
    _wire(sig, monkeypatch,
          submissions={1067983: _submissions([
              ("13F-HR", "acc-latest", "2026-08-14", "2026-06-30"),
              ("13F-HR", "acc-prior", "2026-05-15", "2026-03-31")])},
          infotables=infos)
    return sig


def _run_signal_with(monkeypatch, infos, *, material_add, top_n=10):
    return _signal_with(monkeypatch, infos, material_add=material_add,
                        top_n=top_n).scan(date(2026, 8, 14))


# AAA/OLDCUS shares 100 -> 200 (a 2x add); NEWCUS is a brand-new position.
_MIXED_LATEST = [("Old Co", "OLDCUS", 300, "SH", "", 200),
                 ("New Co", "NEWCUS", 300, "SH", "", 50),
                 ("Big Co", "BIGCUS", 400, "SH", "", 10)]
_MIXED_PRIOR = [("Old Co", "OLDCUS", 200, "SH", "", 100),
                ("Big Co", "BIGCUS", 400, "SH", "", 10)]
_MIXED = {"acc-latest": _infotable(_MIXED_LATEST), "acc-prior": _infotable(_MIXED_PRIOR)}
_ADD_ON = {"enabled": True, "ratio": 1.5, "top_n": 5}


def test_signal_emits_material_adds_with_own_signal_string(monkeypatch):
    ems = _run_signal_with(monkeypatch, _MIXED, material_add=_ADD_ON)
    adds = [e for e in ems if e.signal == "edgar:13f_material_add"]
    assert [e.ticker for e in adds] == ["OLDTKR"]
    assert adds[0].meta["kind"] == "material_add"
    assert adds[0].meta["share_ratio"] == 2.0
    assert adds[0].is_discovery is True
    assert "added to 13F position" in adds[0].evidence


def test_signal_material_add_disabled_is_inert(monkeypatch):
    ems = _run_signal_with(monkeypatch, _MIXED, material_add={"enabled": False})
    assert all(e.signal != "edgar:13f_material_add" for e in ems)
    assert [e.ticker for e in ems] == ["NEWTKR"]      # only the new position


def test_signal_material_add_absent_config_is_inert(monkeypatch):
    """No `material_add` key at all must behave exactly like disabled."""
    ems = _run_signal_with(monkeypatch, _MIXED, material_add=None)
    assert [e.ticker for e in ems] == ["NEWTKR"]


def test_signal_new_positions_are_emitted_before_adds(monkeypatch):
    ems = _run_signal_with(monkeypatch, _MIXED, material_add=_ADD_ON)
    sigs = [e.signal for e in ems]
    assert sigs.index("edgar:13f_new_position") < sigs.index("edgar:13f_material_add")


def test_signal_material_add_top_n_is_independent_of_new_position_top_n(monkeypatch):
    latest = [("A Co", "ACUS", 200, "SH", "", 300), ("B Co", "BCUS", 200, "SH", "", 300),
              ("C Co", "CCUS", 200, "SH", "", 300), ("Big", "BIGCUS", 400, "SH", "", 10)]
    prior = [("A Co", "ACUS", 200, "SH", "", 100), ("B Co", "BCUS", 200, "SH", "", 100),
             ("C Co", "CCUS", 200, "SH", "", 100), ("Big", "BIGCUS", 400, "SH", "", 10)]
    infos = {"acc-latest": _infotable(latest), "acc-prior": _infotable(prior)}
    ems = _run_signal_with(monkeypatch, infos,
                           material_add={"enabled": True, "ratio": 1.5, "top_n": 2})
    adds = [e for e in ems if e.signal == "edgar:13f_material_add"]
    assert len(adds) == 2          # capped at material_add.top_n, not top_n: 10


def test_signal_status_counts_adds_separately_from_new_positions(monkeypatch):
    """The headline count must NOT absorb adds -- spec §6 tells the owner to read this line."""
    sig = _signal_with(monkeypatch, _MIXED, material_add=_ADD_ON)
    sig.scan(date(2026, 8, 14))
    _ran, detail = sig.available()
    assert "1 new 13F positions" in detail      # NOT 2 -- the add must not be counted here
    assert "1 material add" in detail


def test_signal_status_reports_share_count_abstentions(monkeypatch):
    """A position held in both books with no usable share count is a coverage diagnostic."""
    latest = (f'<informationTable xmlns="{_NS}"><infoTable>'
              "<nameOfIssuer>Old Co</nameOfIssuer><titleOfClass>COM</titleOfClass>"
              "<cusip>OLDCUS</cusip><value>500</value>"
              "<shrsOrPrnAmt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
              "</infoTable></informationTable>")
    infos = {"acc-latest": latest,
             "acc-prior": _infotable([("Old Co", "OLDCUS", 400, "SH", "", 100)])}
    sig = _signal_with(monkeypatch, infos, material_add=_ADD_ON)
    sig.scan(date(2026, 8, 14))
    _ran, detail = sig.available()
    assert "1 overlapping positions with unusable share counts" in detail


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
