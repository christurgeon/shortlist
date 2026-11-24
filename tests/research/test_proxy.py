"""Pure-function tests for the DEF 14A proxy reader (compensation & governance
research context line). fetch_proxy (edgartools I/O) is covered by the live
integration test; here we test ProxyFacts + the curated context_line renderer."""
import pytest

from shortlist.research.proxy import ProxyFacts, context_line, _is_real_pct

CFG = {"enabled": True, "max_holders": 3, "control_pct": 30.0}

CAVEAT = "reconcile against the business"   # tail of the always-present caveat


def _facts(**kw) -> ProxyFacts:
    base = dict(ticker="AAPL", accession="0000-1", filing_date="2026-01-08",
                has_xbrl=True)
    base.update(kw)
    return ProxyFacts(**base)


# --- abstention --------------------------------------------------------------

def test_abstains_when_disabled():
    f = _facts(peo_total_comp=74e6)
    assert context_line(f, {"enabled": False, "max_holders": 3}) is None
    assert context_line(f, None) is None


def test_abstains_when_facts_none():
    assert context_line(None, CFG) is None


def test_abstains_when_no_xbrl():
    assert context_line(_facts(has_xbrl=False, peo_total_comp=74e6), CFG) is None


def test_abstains_when_not_usable():
    # has_xbrl True but no comp/ownership content -> nothing to say
    assert context_line(_facts(), CFG) is None


# --- usable() ----------------------------------------------------------------

def test_usable_requires_xbrl_and_some_field():
    assert _facts(peo_total_comp=1.0).usable() is True
    assert _facts(top_holders=[{"name": "Vanguard", "pct": 9.6}]).usable() is True
    assert _facts(has_xbrl=False, peo_total_comp=1.0).usable() is False
    assert _facts().usable() is False


# --- core comp ---------------------------------------------------------------

def test_renders_ceo_comp_and_caveat():
    line = context_line(_facts(peo_name="Tim Cook", peo_total_comp=74_294_811.0,
                               peo_actually_paid_comp=108_400_000.0), CFG)
    assert line is not None
    assert "DEF 14A" in line
    assert "Tim Cook" in line
    assert "$74.3M" in line
    assert "actually-paid" in line and "$108.4M" in line
    assert CAVEAT in line                       # the honest framing is always present
    assert "not a return prediction" in line


# --- CEO pay slice (CPS = CEO total / avg NEO total) --------------------------

def test_cps_property_and_render():
    f = _facts(peo_total_comp=24_000_000.0, neo_avg_total_comp=10_000_000.0)
    assert abs(f.cps - 2.4) < 1e-9
    assert "2.4x avg NEO" in context_line(f, CFG)


def test_cps_omitted_when_neo_missing_or_zero():
    assert _facts(peo_total_comp=24e6, neo_avg_total_comp=None).cps is None
    assert _facts(peo_total_comp=24e6, neo_avg_total_comp=0.0).cps is None
    line = context_line(_facts(peo_total_comp=24e6), CFG)
    assert "avg NEO" not in line


# --- pay ratio (context only, weak evidence) ---------------------------------

def test_pay_ratio_labeled_context():
    line = context_line(_facts(peo_total_comp=74e6, ceo_pay_ratio=533.0), CFG)
    assert "533x median" in line
    assert "context" in line                    # explicitly labeled, never a signal


# --- pay-for-performance alignment -------------------------------------------

def test_pvp_misaligned_comp_up_tsr_down():
    # newest-first: CEO actually-paid comp rose (80 -> 108) while TSR fell (130 -> 90)
    pvp = [{"fy": 2025, "peo_ap": 108e6, "tsr": 90.0},
           {"fy": 2024, "peo_ap": 95e6, "tsr": 110.0},
           {"fy": 2023, "peo_ap": 80e6, "tsr": 130.0}]
    line = context_line(_facts(peo_total_comp=74e6, pvp=pvp), CFG)
    assert "misaligned" in line


def test_pvp_aligned_comp_and_tsr_same_direction():
    pvp = [{"fy": 2025, "peo_ap": 108e6, "tsr": 160.0},
           {"fy": 2023, "peo_ap": 80e6, "tsr": 130.0}]
    line = context_line(_facts(peo_total_comp=74e6, pvp=pvp), CFG)
    assert "aligned" in line and "misaligned" not in line


def test_pvp_omitted_when_insufficient_rows():
    line = context_line(_facts(peo_total_comp=74e6,
                               pvp=[{"fy": 2025, "peo_ap": 108e6, "tsr": 90.0}]), CFG)
    assert "pay-for-performance" not in line


# --- ownership concentration --------------------------------------------------

def test_top_holders_rendered_and_capped():
    holders = [{"name": "Vanguard", "pct": 9.6}, {"name": "BlackRock", "pct": 7.1},
               {"name": "State Street", "pct": 4.0}, {"name": "Fidelity", "pct": 3.5}]
    line = context_line(_facts(top_holders=holders), {"enabled": True, "max_holders": 2})
    assert "Vanguard 9.6%" in line and "BlackRock 7.1%" in line
    assert "State Street" not in line           # capped at max_holders=2


def test_control_note_when_concentrated():
    line = context_line(_facts(top_holders=[{"name": "M Zuckerberg", "pct": 60.8}]), CFG)
    assert "60.8%" in line
    assert "control" in line.lower()            # founder/controlling-stake note
    assert "double-edged" in line               # the honest both-ways framing


def test_no_control_note_for_diffuse_ownership():
    line = context_line(_facts(top_holders=[{"name": "Vanguard", "pct": 9.6}]), CFG)
    assert "concentrated control" not in line


# --- governance hygiene -------------------------------------------------------

def test_governance_hygiene_notes():
    line = context_line(_facts(peo_total_comp=74e6, insider_trading_policy=False,
                               award_timing_concern=True), CFG)
    assert "insider-trading policy" in line.lower()
    assert "award" in line.lower()


# --- the sentinel guard (used by fetch_proxy on the 5%+ ownership table) ------

def test_is_real_pct_drops_sentinel_and_junk():
    assert _is_real_pct(9.6) is True
    assert _is_real_pct(60.8) is True
    assert _is_real_pct(0.5) is False           # edgartools "<1%/*" sentinel
    assert _is_real_pct(None) is False
    assert _is_real_pct(0.0) is False
    assert _is_real_pct(float("nan")) is False
    assert _is_real_pct(150.0) is False         # implausible (>100%)


# --- _facts_from_proxy extraction (fake duck-typed proxy; no network/pandas) ---

from shortlist.research.proxy import _facts_from_proxy


class _DF:
    """Minimal pandas-DataFrame stand-in: .to_dict('records') -> list[dict]."""
    def __init__(self, records):
        self._records = records

    def to_dict(self, orient):
        return list(self._records)


class _Ratio:
    def __init__(self, ratio):
        self.ratio = ratio


class _Proxy:
    """Duck-typed ProxyStatement stand-in matching edgartools' attribute surface."""
    def __init__(self, **kw):
        d = dict(has_xbrl=True, peo_name=None, peo_total_comp=None,
                 peo_actually_paid_comp=None, neo_avg_total_comp=None,
                 ceo_pay_ratio=None, pay_vs_performance=_DF([]),
                 beneficial_ownership=_DF([]), insider_trading_policy_adopted=None,
                 mnpi_disclosure_timed_for_comp_value=None)
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


def test_facts_from_proxy_maps_scalars():
    from decimal import Decimal
    p = _Proxy(peo_name="Tim Cook", peo_total_comp=Decimal("74294811"),
               peo_actually_paid_comp=Decimal("108400000"),
               neo_avg_total_comp=Decimal("30000000"), ceo_pay_ratio=_Ratio(533))
    f = _facts_from_proxy("AAPL", "acc-1", "2026-01-08", p)
    assert f.has_xbrl is True and f.usable() is True
    assert f.peo_name == "Tim Cook"
    assert abs(f.peo_total_comp - 74294811.0) < 1     # Decimal -> float
    assert abs(f.cps - (74294811 / 30000000)) < 1e-6
    assert f.ceo_pay_ratio == 533.0


def test_facts_from_proxy_unusable_without_xbrl():
    f = _facts_from_proxy("X", "a", "d", _Proxy(has_xbrl=False, peo_total_comp=1.0))
    assert f.has_xbrl is False and f.usable() is False


def test_facts_from_proxy_pvp_newest_first():
    # source DataFrame is oldest-first (edgartools sorts ascending by fiscal_year_end)
    df = _DF([{"fiscal_year_end": "2023-12-31", "peo_actually_paid_comp": 80e6,
               "neo_avg_actually_paid_comp": 10e6, "total_shareholder_return": 130.0,
               "peer_group_tsr": 120.0, "net_income": 9e10},
              {"fiscal_year_end": "2025-12-31", "peo_actually_paid_comp": 108e6,
               "neo_avg_actually_paid_comp": 12e6, "total_shareholder_return": 90.0,
               "peer_group_tsr": 140.0, "net_income": 11e10}])
    f = _facts_from_proxy("AAPL", "a", "d", _Proxy(peo_total_comp=1.0, pay_vs_performance=df))
    assert f.pvp[0]["fy"] == 2025 and f.pvp[-1]["fy"] == 2023   # newest-first
    assert abs(f.pvp[0]["peo_ap"] - 108e6) < 1


def test_facts_from_proxy_ownership_5pct_only_and_sentinel_dropped():
    df = _DF([{"holder_name": "Vanguard", "holder_type": "5pct_holder",
               "shares": 1, "percent_of_class": 9.63},
              {"holder_name": "A Director", "holder_type": "director_officer",
               "shares": 1, "percent_of_class": 0.5},     # wrong type -> drop
              {"holder_name": "MysteryFund", "holder_type": "5pct_holder",
               "shares": 1, "percent_of_class": 0.5}])     # sentinel in 5pct table -> drop
    f = _facts_from_proxy("AAPL", "a", "d", _Proxy(beneficial_ownership=df))
    assert [h["name"] for h in f.top_holders] == ["Vanguard"]


def test_facts_from_proxy_award_timing_concern():
    on = _Proxy(peo_total_comp=1.0, mnpi_disclosure_timed_for_comp_value=True)
    assert _facts_from_proxy("X", "a", "d", on).award_timing_concern is True
    off = _Proxy(peo_total_comp=1.0, mnpi_disclosure_timed_for_comp_value=None)
    assert _facts_from_proxy("X", "a", "d", off).award_timing_concern is False


# --- assess.py wiring: prompt-only injection + byte-identity when disabled ----

from shortlist.research import assess
from shortlist.research.models import FilingBundle, FilingText, default_valid_signals


def _bundle() -> FilingBundle:
    tenk = FilingText(ticker="AAPL", accession="acc", filing_date="2026-01-08",
                      business="biz text", mda="mda text", risk_factors="rf text")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-01-08")


_ENABLED = {"research": {"proxy": {"enabled": True, "max_holders": 3, "control_pct": 30.0}}}


def test_proxy_line_in_prompt_but_not_haystack():
    facts = _facts(peo_name="Tim Cook", peo_total_comp=74e6)
    b = _bundle()
    prompt = assess._build_user_prompt(b, _ENABLED, None, proxy_facts=facts)
    assert "Proxy (DEF 14A" in prompt and "Tim Cook" in prompt
    # the grounding corpus must NEVER contain the proxy line (quote-verification guard)
    assert "Proxy (DEF 14A" not in b.haystack()


def test_proxy_line_absent_when_disabled():
    facts = _facts(peo_name="Tim Cook", peo_total_comp=74e6)
    cfg = {"research": {"proxy": {"enabled": False}}}
    assert "Proxy (DEF 14A" not in assess._build_user_prompt(_bundle(), cfg, None,
                                                             proxy_facts=facts)


def test_proxy_line_absent_when_block_missing():
    facts = _facts(peo_name="Tim Cook", peo_total_comp=74e6)
    assert "Proxy (DEF 14A" not in assess._build_user_prompt(_bundle(), {"research": {}},
                                                             None, proxy_facts=facts)


def test_proxy_line_absent_when_no_facts():
    assert "Proxy (DEF 14A" not in assess._build_user_prompt(_bundle(), _ENABLED, None,
                                                             proxy_facts=None)


def test_governance_is_a_valid_reconciliation_signal():
    assert "governance" in default_valid_signals()


# --- review fixes ------------------------------------------------------------

def test_top_holders_sorted_largest_first_in_render():
    # input order is NOT descending; the rendered top-N must be largest-first
    holders = [{"name": "Small", "pct": 3.0}, {"name": "Big", "pct": 9.6},
               {"name": "Mid", "pct": 7.1}]
    line = context_line(_facts(top_holders=holders), {"enabled": True, "max_holders": 2})
    assert "Big 9.6%, Mid 7.1%" in line
    assert "Small" not in line


def test_negative_actually_paid_renders_signed():
    # Item 402(v) "compensation actually paid" can be negative (underwater awards) —
    # it must NOT be abs()'d to a positive figure (that flips the alignment signal)
    line = context_line(_facts(peo_total_comp=74e6, peo_actually_paid_comp=-20_000_000.0), CFG)
    assert "actually-paid -$20.0M" in line


def test_total_comp_still_unsigned():
    line = context_line(_facts(peo_total_comp=74_294_811.0), CFG)
    assert "comp $74.3M" in line and "-$74" not in line


def test_usable_with_only_pvp():
    pvp = [{"fy": 2025, "peo_ap": 108e6, "tsr": 90.0},
           {"fy": 2023, "peo_ap": 80e6, "tsr": 130.0}]
    f = _facts(pvp=pvp)
    assert f.usable() is True
    line = context_line(f, CFG)
    assert line is not None and "pay-for-performance" in line


def test_extract_pvp_orders_newest_first_regardless_of_source_order():
    # rows arrive OUT of order; result must still be newest-first (not positional reverse)
    df = _DF([{"fiscal_year_end": "2024-12-31", "peo_actually_paid_comp": 95e6,
               "total_shareholder_return": 110.0},
              {"fiscal_year_end": "2026-12-31", "peo_actually_paid_comp": 120e6,
               "total_shareholder_return": 100.0},
              {"fiscal_year_end": "2023-12-31", "peo_actually_paid_comp": 80e6,
               "total_shareholder_return": 130.0}])
    f = _facts_from_proxy("X", "a", "d", _Proxy(peo_total_comp=1.0, pay_vs_performance=df))
    assert [r["fy"] for r in f.pvp] == [2026, 2024, 2023]


# --- _pick_latest (pure PiT/exact-form selection; no network) -----------------

from shortlist.research.proxy import _pick_latest
import contextlib


class _Filing:
    def __init__(self, form, filing_date):
        self.form = form
        self.filing_date = filing_date


def test_pick_latest_exact_form_and_newest():
    fs = [_Filing("DEF 14A", "2024-01-10"), _Filing("DEFA14A", "2025-06-01"),
          _Filing("DEF 14A", "2026-01-08")]
    picked = _pick_latest(fs, None)
    assert picked is not None and picked.filing_date == "2026-01-08"   # DEFA14A ignored


def test_pick_latest_point_in_time_excludes_future():
    fs = [_Filing("DEF 14A", "2024-01-10"), _Filing("DEF 14A", "2026-01-08")]
    assert _pick_latest(fs, "2025-01-01").filing_date == "2024-01-10"  # 2026 excluded
    assert _pick_latest(fs, "2020-01-01") is None                      # nothing at/ before


# --- assess(): PROXY_SYSTEM_ADDENDUM is gated on proxy.enabled ----------------

class _AssessCard:
    metrics = None
    composite = None
    gates: list = []
    flags: list = []
    def __init__(self):
        for k in ("quality", "moat", "growth", "momentum", "value", "insider",
                  "risk", "confidence", "sic_bucket"):
            setattr(self, k, None)


def _run_assess_capture(monkeypatch, proxy_enabled):
    cap = {}

    def runner(prompt, system, model, timeout_s):
        cap["prompt"], cap["system"] = prompt, system
        raise RuntimeError("halt before CLI parse")

    monkeypatch.setattr(assess.proxy_ctx, "fetch_proxy",
                        lambda *a, **k: _facts(peo_total_comp=74e6))
    cfg = {"research": {"screening_call": {"enabled": False},
                        "proxy": {"enabled": proxy_enabled, "max_holders": 3,
                                  "control_pct": 30.0}}}
    with contextlib.suppress(Exception):
        assess.assess(_AssessCard(), _bundle(), cfg, runner=runner)
    return cap


def test_system_addendum_and_line_present_when_enabled(monkeypatch):
    cap = _run_assess_capture(monkeypatch, proxy_enabled=True)
    assert assess.PROXY_SYSTEM_ADDENDUM in cap["system"]
    assert "Proxy (DEF 14A" in cap["prompt"]


def test_system_addendum_and_line_absent_when_disabled(monkeypatch):
    cap = _run_assess_capture(monkeypatch, proxy_enabled=False)
    assert assess.PROXY_SYSTEM_ADDENDUM not in cap["system"]
    assert "Proxy (DEF 14A" not in cap["prompt"]


# --- holder names: edgartools glues a cell's name and address lines together ---

_OWNERSHIP_HTML = """<html><body>
<p>Security Ownership of Certain Beneficial Owners and Management</p>
<table>
<tr><td>Name and Address of Beneficial Owner</td>
    <td>Number of Shares</td><td>Percent of Class</td></tr>
<tr><td><div><span>The Vanguard Group</span></div><div>100 Vanguard Boulevard</div>
        <div>Malvern, PA 19355</div></td><td>240,000,000</td><td>10.1%</td></tr>
<tr><td><div><span>BlackRock, Inc.</span><span>(2)</span></div>
        <div>50 Hudson Yards, New York, New York 10001</div></td>
    <td>210,000,000</td><td>8.9%</td></tr>
<tr><td>State Street Corporation<br/>One Congress Street<br/>Boston, MA 02114</td>
    <td>140,000,000</td><td>6.0%</td></tr>
<tr><td>Berkshire Hathaway&#160;Inc.</td><td>400,000,000</td><td>9.3%</td></tr>
<tr><td><div>A Director</div><div>Chief Executive Officer</div></td>
    <td>1,000</td><td>*</td></tr>
</table></body></html>"""


def _real_ownership_proxy(html=_OWNERSHIP_HTML):
    pytest.importorskip("edgar")
    import lxml.html
    from edgar.proxy.html_extractor import extract_beneficial_ownership

    tree = lxml.html.fromstring(html)
    owners = extract_beneficial_ownership(tree) or []
    df = _DF([{"holder_name": o.name, "holder_type": o.holder_type, "shares": o.shares,
               "percent_of_class": o.percent_of_class} for o in owners])
    p = _Proxy(beneficial_ownership=df)
    p._html_tree = tree
    return p


def test_holder_names_drop_glued_addresses():
    f = _facts_from_proxy("X", "a", "d", _real_ownership_proxy())
    assert [(h["name"], h["pct"]) for h in f.top_holders] == [
        ("The Vanguard Group", 10.1),
        ("BlackRock, Inc.", 8.9),
        ("State Street Corporation", 6.0),
        ("Berkshire Hathaway Inc.", 9.3),
    ]


def test_holder_names_kept_when_tree_unavailable():
    p = _real_ownership_proxy()
    p._html_tree = None
    names = [h["name"] for h in _facts_from_proxy("X", "a", "d", p).top_holders]
    assert names[0].startswith("The Vanguard Group100 Vanguard Boulevard")
    assert len(names) == 4


def test_holder_name_only_shortened_by_its_own_cell():
    from shortlist.research.proxy import _holder_name
    cells = [["BlackRock", "Footnote: shares held by subsidiaries"],
             ["Capital Research (USA)", "333 South Hope Street"]]
    assert _holder_name("BlackRock, Inc.", cells) == "BlackRock, Inc."
    assert _holder_name("Capital Research (USA)333 South Hope Street",
                        cells) == "Capital Research (USA)"
    assert _holder_name("FMR LLC(3)", [["FMR LLC(3)"]]) == "FMR LLC(3)"


def test_holder_name_ignores_source_newlines_and_keeps_wrapped_names():
    pytest.importorskip("lxml")
    import lxml.html
    from shortlist.research.proxy import _cell_lines, _holder_name

    def name_of(cell_html, glued):
        lines = _cell_lines(lxml.html.fragment_fromstring(cell_html))
        return _holder_name(glued, [lines])

    assert name_of("<td><p>BlackRock,\n   Inc.  (2)</p><p>55 East 52nd St</p></td>",
                   "BlackRock, Inc. (2)55 East 52nd St") == "BlackRock, Inc."
    assert name_of("<td><div>Capital Research and</div><div>Management Company</div>"
                   "<div>333 South Hope Street</div></td>",
                   "Capital Research andManagement Company333 South Hope Street"
                   ) == "Capital Research and Management Company"
    assert name_of("<td>T. Rowe Price Associates,<br>Inc.<br>100 E. Pratt Street</td>",
                   "T. Rowe Price Associates,Inc.100 E. Pratt Street"
                   ) == "T. Rowe Price Associates, Inc."
    assert name_of("<td><div>State Farm Mutual Automobile</div>"
                   "<div>Insurance Company</div></td>",
                   "State Farm Mutual AutomobileInsurance Company"
                   ) == "State Farm Mutual Automobile Insurance Company"
    assert name_of("<td><div>State Street Corporation</div>"
                   "<div>State Street Financial Center</div><div>One Lincoln Street</div>"
                   "<div>Boston, MA 02111</div></td>",
                   "State Street CorporationState Street Financial CenterOne Lincoln "
                   "StreetBoston, MA 02111") == "State Street Corporation"


@pytest.mark.parametrize("lines, name", [
    (["The Vanguard Group,", "100 Vanguard Blvd.", "Malvern, PA 19355"], "The Vanguard Group"),
    (["Acme Holdings,", "P.O. Box 1234", "Dover, DE 19901"], "Acme Holdings"),
    (["Acme Holdings,", "Wilmington, DE 19801"], "Acme Holdings"),
    (["Wellington Management Group", "LLP", "280 Congress Street"],
     "Wellington Management Group LLP"),
    (["State Street Corporation and its", "affiliates", "One Congress Street"],
     "State Street Corporation and its affiliates"),
])
def test_wrapped_name_stops_at_the_address(lines, name):
    from shortlist.research.proxy import _name_from_lines
    assert _name_from_lines(lines) == name
