"""Pure-function tests for the DEF 14A proxy reader (compensation & governance
research context line). fetch_proxy (edgartools I/O) is covered by the live
integration test; here we test ProxyFacts + the curated context_line renderer."""
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
