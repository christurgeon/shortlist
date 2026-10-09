"""The "ROIC persistence" section: what it says, what it must always say, and that fetching it
can never cost a brief."""
import inspect
import json
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from shortlist import durability_profile as dp
from shortlist import research
from shortlist.config import load_config
from shortlist.research import assess as assess_mod
from shortlist.research import cachekey, report
from shortlist.research import durability as rd
from shortlist.research.models import FilingBundle, FilingText, Moat, QualitativeAssessment, Thesis

TODAY = date(2026, 3, 15)
TABLE = dp.load_table()
CIK = 1234
CONFIG = {"sectors": {"buckets": [{"name": "financial", "sic_ranges": [[6000, 6799]]}]}}
HIGH = (400.0, 400.0)                       # operating income, equity: a ROIC of 79%


def _profile(**over):
    base = {"period_end": "2025-12-31", "bucket": 2025, "reference_year": 2025, "roic": 0.312,
            "floor": TABLE["floors"][2025], "universe_n": len(TABLE["universe"][2025]),
            "has_debt": True, "capital_growth": 0.34, "investment_third": 0,
            "stability_spread": 0.021, "stability_third": 2, "years_seen": 4}
    return dp.DurabilityProfile(**{**base, **over})


# ---------------------------------------------------------------- the words

def test_the_section_quotes_the_verdicts_numbers():
    text = rd.brief_section(_profile(), TABLE)
    for want in ("ROIC 31.2% for the year ended 2025-12-31",
                 "above 15.5%, the top-fifth cutoff among 1,787 non-financial 10-K filers",
                 "59% (cohorts formed 2011-2017) and 54% (2018-2021)",
                 "not counted: 16% and 11% of those cohorts",
                 "invested capital +34% in the last year: the fastest-growing third",
                 "by 15 points (95% interval 6 to 24) in the 2011-2017 cohorts and 20 points (9 to 31) in 2018-2021",
                 "(54%, 59%) is capital that kept growing slowly",
                 "varied by 2.1 points over the 4 years with a ROIC out of its last four: the steadiest third",
                 "by 15 points (95% interval 3 to 26) in the 2011-2017 cohorts and 22 points (9 to 35) in 2018-2021",
                 "By cohort year in 2018-2021: -4, -8, +35, +62 points",
                 "Six predictors were tested. These two passed"):
        assert want in text, want


def test_the_caveats_the_verdict_requires_are_always_there():
    text = rd.brief_section(_profile(), TABLE)
    lines = text.split("\n")
    assert lines[0] == ("Historical frequencies for an accounting ratio. Not a forecast for this "
                        "company. It says nothing about moat, price or returns.")
    growth = next(ln for ln in lines if ln.startswith("- Capital growth:"))
    for clause in ("across all sectors, not within a sector",
                   "same two-digit SIC sector and cohort year, with ROIC rank and revenue rank held fixed",
                   "on a straight-line fit across the rank range",
                   "A little over half of the link",
                   "positive but not distinguishable from zero in 2018-2021",
                   "Not tested: acquired capital, retained cash, a one-year profit spike",
                   "a pattern in the ratio, not a finding about the business"):
        assert clause in growth, clause
    steady = next(ln for ln in lines if ln.startswith("- ROIC steadiness"))
    for clause in ("a track-record measure: a longer view of the same ROIC level, not a separate trait",
                   "The later result comes from the 2020 and 2021 cohorts; 2018 and 2019 went the other way"):
        assert clause in steady, clause
    cohorts = next(ln for ln in lines if ln.startswith("- Past cohorts:"))
    assert "still reported usable annual data" in cohorts and "are not counted" in cohorts
    assert "a group average, not adjusted for this company's ROIC level" in cohorts


def test_the_section_never_calls_it_a_moat_or_a_good_sign():
    for prof in (_profile(), _profile(investment_third=2, stability_third=0),
                 _profile(investment_third=1, stability_third=1)):
        text = rd.brief_section(prof, TABLE).lower()
        assert text.count("moat") == 1 and "nothing about moat" in text
        assert "favourable" not in text and "favorable" not in text
        assert "durab" not in text and "forecast for this company" in text
    assert "moat" not in rd.HEADING.lower()


@pytest.mark.parametrize("third, label", [(0, "the fastest-growing third"), (1, "the middle third"),
                                          (2, "the slowest-growing third")])
def test_capital_growth_thirds_have_neutral_names(third, label):
    assert label in rd.brief_section(_profile(investment_third=third), TABLE)


@pytest.mark.parametrize("third, label", [(0, "the least steady third"), (1, "the middle third"),
                                          (2, "the steadiest third")])
def test_steadiness_thirds_have_neutral_names(third, label):
    line = next(ln for ln in rd.brief_section(_profile(stability_third=third), TABLE).split("\n")
                if ln.startswith("- ROIC steadiness"))
    assert label in line


def test_a_predictor_that_cannot_be_computed_drops_its_line():
    no_growth = rd.brief_section(_profile(capital_growth=None, investment_third=None), TABLE)
    assert "- Capital growth:" not in no_growth and "- ROIC steadiness" in no_growth
    no_steady = rd.brief_section(_profile(stability_spread=None, stability_third=None), TABLE)
    assert "- ROIC steadiness" not in no_steady and "- Capital growth:" in no_steady
    assert "- Past cohorts:" in no_growth and "- Past cohorts:" in no_steady


def test_the_company_line_says_when_the_reading_is_weaker():
    plain = rd.brief_section(_profile(), TABLE)
    for clause in ("not built yet", "within 1 point of the cutoff", "book equity"):
        assert clause not in plain
    assert "The cutoff for this company's own fiscal year is not built yet." in rd.brief_section(
        _profile(bucket=2026, period_end="2026-12-31"), TABLE)
    floor = TABLE["floors"][2025]
    assert "It is within 1 point of the cutoff." in rd.brief_section(_profile(roic=floor + 0.0099), TABLE)
    assert "within 1 point" not in rd.brief_section(_profile(roic=floor + 0.0101), TABLE)
    assert ("No debt is reported in the tagged data, so invested capital here is book equity."
            in rd.brief_section(_profile(has_debt=False), TABLE))


@pytest.mark.parametrize("growth, want", [(0.004, "invested capital 0% in"), (-0.004, "invested capital 0% in"),
                                          (-0.105, "invested capital -11% in"), (0.125, "invested capital +13% in"),
                                          (15.39, "invested capital +1539% in")])
def test_capital_growth_rounds_half_up_and_never_prints_minus_zero(growth, want):
    assert want in rd.brief_section(_profile(capital_growth=growth), TABLE)


def test_rounding_is_half_up_on_the_printed_decimal():
    assert [str(rd._round(x)) for x in (0.5, 1.5, 2.5, -0.5, -0.3, 0.0)] == ["1", "2", "3", "-1", "0", "0"]
    assert rd._pct(0.155325, 1) == "15.5%" and rd._pct(0.585) == "59%" and rd._pct(0.5849) == "58%"
    assert rd._signed_points(-0.0369) == "-4" and rd._signed_points(0.3466) == "+35"


def test_every_reason_has_one_sentence_and_no_other():
    assert set(rd._REASONS) == set(dp.NOT_SHOWN)
    detail = {"roic": 0.091, "floor": 0.1553, "period_end": "2025-12-31", "table_year": 2025}
    for reason in dp.NOT_SHOWN:
        text = rd.not_shown(reason, detail)
        assert text.startswith("Not shown: ") and text.endswith("Absence says nothing about this company.")
        assert "{" not in text and "moat" not in text.lower()
    assert "ROIC 9.1% for the year ended 2025-12-31 (the study's basis) is below the 15.5% top-fifth cutoff" in (
        rd.not_shown(dp.NOT_TOP_FIFTH, detail))


# ---------------------------------------------------------------- the fetch

def _body(years, *, cik=CIK, form="10-K", lag=50):
    """A raw company-facts body: `years` is {year of a Dec year end: (operating income, equity)}."""
    tags = {t: [] for t in ("Revenues", "OperatingIncomeLoss", "StockholdersEquity", "Assets", "Goodwill")}
    for y, (oi, eq) in years.items():
        end = date(y, 12, 31)
        extra = {"filed": (end + timedelta(days=lag)).isoformat(), "form": form,
                 "accn": "0000000000-26-000001", "fy": y, "fp": "FY", "frame": f"CY{y}"}
        dur = {"start": (end - timedelta(days=364)).isoformat(), "end": end.isoformat(), **extra}
        inst = {"end": end.isoformat(), **extra}
        tags["Revenues"].append({**dur, "val": 5e8})
        tags["OperatingIncomeLoss"].append({**dur, "val": oi})
        tags["StockholdersEquity"].append({**inst, "val": eq})
        tags["Assets"].append({**inst, "val": 1000.0})
        tags["Goodwill"].append({**inst, "val": 10.0})
    return {"cik": cik, "entityName": "TEST CO",
            "facts": {"dei": {}, "us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items()}}}


FOUR_YEARS = dict.fromkeys(range(2022, 2026), HIGH)


class _Net:
    """A fake sec.gov: counts the requests and records what was sent."""

    def __init__(self, response):
        self.response, self.requests = response, []

    def __call__(self, request):
        self.requests.append(request)
        r = self.response
        return r(request) if callable(r) else r

    @property
    def transport(self):
        return httpx.MockTransport(self)


def _json(body, status=200):
    return httpx.Response(status, json=body)


@pytest.fixture
def env(tmp_path, monkeypatch):
    labels = []
    monkeypatch.setenv("SEC_IDENTITY", "tester@example.com")
    monkeypatch.setattr(rd, "sec_throttle", lambda: labels.append)
    monkeypatch.setattr(rd, "_cik", lambda ticker: CIK)
    config = {**CONFIG, "research": {"durability": {"enabled": True, "cache_dir": str(tmp_path / "cache")}}}
    return SimpleNamespace(labels=labels, config=config, cache=tmp_path / "cache")


def _card(sic="3674", ticker="TEST"):
    return SimpleNamespace(ticker=ticker, metrics=SimpleNamespace(sic=sic))


def _bundle(filed="2026-02-19"):
    tenk = FilingText("TEST", "acc-1", filed, business="b")
    return FilingBundle(tenk=tenk, primary_accession="acc-1", cache_key="acc-1", filing_date=filed)


def _fetch(env, net, card=None, bundle=None, today=TODAY):
    return rd.fetch_section(card or _card(), bundle or _bundle(), env.config, today=today,
                            transport=net.transport)


def test_one_throttled_request_gives_the_section(env):
    net = _Net(_json(_body(FOUR_YEARS)))
    text, status = _fetch(env, net)
    assert status == dp.SHOWN and "- This company: ROIC 79.0% for the year ended 2025-12-31" in text
    assert env.labels == ["durability"]             # the one process-wide sec.gov budget, once
    (request,) = net.requests
    assert str(request.url) == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000001234.json"
    assert request.headers["user-agent"] == "tester@example.com"


def test_the_day_cache_holds_the_compacted_record_and_serves_the_next_call(env):
    net = _Net(_json(_body(FOUR_YEARS)))
    first = _fetch(env, net)
    (cached,) = env.cache.iterdir()
    assert cached.name == "CIK0000001234-2026-03-15.json"
    record = json.loads(cached.read_text())
    assert "Goodwill" not in record["facts"]["us-gaap"] and "dei" not in record["facts"]
    assert set(record["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]) == {
        "start", "end", "val", "filed", "form"}
    assert _fetch(env, net) == first
    assert len(net.requests) == 1 and env.labels == ["durability"]
    # The next day asks again, and the old day is kept for a week, then removed.
    _fetch(env, net, today=TODAY + timedelta(days=1))
    assert len(net.requests) == 2 and len(list(env.cache.iterdir())) == 2
    _fetch(env, net, today=TODAY + timedelta(days=9))
    assert [p.name for p in env.cache.iterdir()] == ["CIK0000001234-2026-03-24.json"]


@pytest.mark.parametrize("response", [
    httpx.Response(403, text="Forbidden"),
    httpx.Response(429, text="Too many requests"),
    httpx.Response(200, text="<html><body>Your Request Originates from an Undeclared Automated Tool</body></html>"),
    _json({"message": "Internal server error"}),
    _json([1, 2, 3]),
    httpx.Response(200, text=json.dumps(_body(FOUR_YEARS))[:500]),             # cut short
    _json(_body(FOUR_YEARS, cik=999)),                                         # another company
    _json({"cik": CIK, "facts": []}),
], ids=["403", "429", "html", "json-error", "list", "truncated", "wrong-cik", "facts-list"])
def test_a_bad_response_is_unavailable_and_is_not_cached(env, capsys, response):
    text, status = _fetch(env, _Net(response))
    assert (text, status) == (rd.not_shown(dp.UNAVAILABLE), dp.UNAVAILABLE)
    err = capsys.readouterr().err
    assert err.count("\n") == 1 and "ROIC-persistence section failed for TEST" in err
    assert not env.cache.exists() or not list(env.cache.iterdir())


@pytest.mark.parametrize("damage", [
    lambda rows: rows[-1].update(val="n/a"),
    lambda rows: rows[-1].update(val={"x": 1}),
    lambda rows: rows[-1].update(filed=20260219),
    lambda rows: rows.append("not a row"),
], ids=["val-str", "val-dict", "filed-int", "row-str"])
def test_malformed_facts_cost_the_section_and_never_the_brief(env, capsys, damage):
    body = _body(FOUR_YEARS)
    damage(body["facts"]["us-gaap"]["OperatingIncomeLoss"]["units"]["USD"])
    assert _fetch(env, _Net(_json(body)))[1] == dp.UNAVAILABLE
    assert capsys.readouterr().err.count("\n") == 1


def test_without_an_identity_no_request_is_made(env, monkeypatch):
    monkeypatch.delenv("SEC_IDENTITY")
    net = _Net(_json(_body(FOUR_YEARS)))
    assert _fetch(env, net)[1] == dp.UNAVAILABLE
    assert net.requests == [] and env.labels == []


def test_the_error_line_is_redacted(env, capsys, monkeypatch):
    monkeypatch.setattr(rd, "_cik", lambda t: (_ for _ in ()).throw(RuntimeError("GET /x?apikey=SECRET")))
    monkeypatch.setattr(rd, "redact_secrets", lambda s: s.replace("SECRET", "[redacted]"))
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.UNAVAILABLE
    err = capsys.readouterr().err
    assert "SECRET" not in err and "[redacted]" in err


def test_a_cache_directory_that_cannot_be_written_does_not_lose_the_section(env):
    env.cache.write_text("a file where the directory should be")
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.SHOWN


def test_a_response_over_the_size_cap_is_dropped(env, monkeypatch):
    monkeypatch.setattr(rd, "MAX_BYTES", 100)
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.UNAVAILABLE


def test_a_response_slower_than_the_deadline_is_dropped(env, monkeypatch):
    clock = iter([0.0, 16.0, 17.0, 18.0])
    monkeypatch.setattr(rd.time, "monotonic", lambda: next(clock))
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.UNAVAILABLE


def test_a_bank_gets_its_reason_without_a_request(env, monkeypatch):
    monkeypatch.setattr(rd, "_cik", lambda t: pytest.fail("no CIK lookup for a masked sector"))
    net = _Net(_json(_body(FOUR_YEARS)))
    assert _fetch(env, net, card=_card(sic="6021")) == (
        rd.not_shown(dp.SECTOR_NOT_COVERED), dp.SECTOR_NOT_COVERED)
    assert _fetch(env, net, card=_card(sic=None))[1] == dp.SIC_UNKNOWN
    assert _fetch(env, net, card=SimpleNamespace(ticker="X"))[1] == dp.SIC_UNKNOWN     # no metrics
    assert net.requests == []


def test_no_table_is_unavailable_without_a_request(env, monkeypatch):
    monkeypatch.setattr(rd, "load_table", lambda: None)
    net = _Net(_json(_body(FOUR_YEARS)))
    assert _fetch(env, net)[1] == dp.UNAVAILABLE and net.requests == []


def test_a_company_the_study_does_not_cover_gets_its_reason(env):
    low = {**FOUR_YEARS, 2025: (50.0, 400.0)}                   # a ROIC of 9.9%
    text, status = _fetch(env, _Net(_json(_body(low))))
    assert status == dp.NOT_TOP_FIFTH and "ROIC 9.9% for the year ended 2025-12-31" in text
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS, form="20-F"))), today=TODAY + timedelta(days=1)) == (
        rd.not_shown(dp.NO_ANNUAL_FACTS), dp.NO_ANNUAL_FACTS)


def test_facts_cached_before_the_10k_landed_are_fetched_again(env):
    # The morning run cached facts up to 2025. The 10-K for 2026 was filed that afternoon and
    # the brief is written from it: a section for 2025 would be a year old.
    today = date(2027, 2, 20)
    env.cache.mkdir()
    old = rd.compact_facts(_body(FOUR_YEARS))
    (env.cache / f"CIK{CIK:010d}-{today.isoformat()}.json").write_text(json.dumps(old))
    net = _Net(_json(_body({y + 1: v for y, v in FOUR_YEARS.items()})))
    text, status = _fetch(env, net, bundle=_bundle(filed="2027-02-20"), today=today)
    assert status == dp.SHOWN and "for the year ended 2026-12-31" in text
    assert len(net.requests) == 1


def test_facts_that_still_lag_the_10k_are_not_shown(env):
    today = date(2027, 2, 20)
    net = _Net(_json(_body(FOUR_YEARS)))                        # the API has not caught up
    assert _fetch(env, net, bundle=_bundle(filed="2027-02-20"), today=today) == (
        rd.not_shown(dp.FACTS_LAG_FILING), dp.FACTS_LAG_FILING)
    assert len(net.requests) == 2                               # once, then once past the cache


def test_a_10k_filed_within_the_normal_window_is_not_a_lag(env):
    net = _Net(_json(_body(FOUR_YEARS)))
    # 300 days after the year end is the limit; a late filer is the study's business, not a lag.
    assert _fetch(env, net, bundle=_bundle(filed="2026-10-27"), today=date(2026, 11, 1))[1] == dp.SHOWN
    assert len(net.requests) == 1


def test_a_bundle_with_no_filing_date_is_not_a_lag(env):
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))), bundle=SimpleNamespace())[1] == dp.SHOWN


# ---------------------------------------------------------------- wiring

def _assessment():
    return QualitativeAssessment(ticker="TEST", as_of="t", filing_accession="acc-1",
                                 filing_date="2026-02-19", model="m", moat=Moat(),
                                 thesis=Thesis(takeaway="read."), cache_key="acc-1")


def _rank_card():
    return SimpleNamespace(ticker="TEST", composite=90.0, confidence=1.0, scored=True, gates=[],
                           passed=True, metrics=SimpleNamespace(sic="3674"))


def _enrich(tmp_path, monkeypatch, *, enabled, section=("- This company: a body", dp.SHOWN)):
    calls = []

    def fake_assess(card, bundle, config, **kw):
        calls.append("assess")
        return _assessment()

    def fake_section(card, bundle, config):
        calls.append("section")
        return section() if callable(section) else section

    monkeypatch.setattr(rd, "fetch_section", fake_section)
    config = {"research": {"output_root": str(tmp_path), "durability": {"enabled": enabled}}}
    (result,) = research.enrich([_rank_card()], config, top_n=1, fetch=lambda t, **k: _bundle(),
                                assess_fn=fake_assess)
    return result, calls


def test_the_section_is_off_unless_enabled(tmp_path, monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch, enabled=False)
    assert calls == ["assess"]
    brief = Path(result.brief_path).read_text()
    assert "ROIC persistence" not in brief
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert (record["durability_line"], record["durability_status"]) == ("", "")
    assert brief == report.to_markdown(_assessment())      # byte-identical to a brief without it


def test_the_section_is_computed_after_the_model_call_and_printed(tmp_path, monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch, enabled=True)
    assert calls == ["assess", "section"]           # after: the model must never see it
    brief = Path(result.brief_path).read_text()
    assert f"## {rd.HEADING}\n- This company: a body\n" in brief
    assert brief.index("## Reconciliation") < brief.index(rd.HEADING) < brief.index("## Moat\n")
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert (record["durability_line"], record["durability_status"]) == ("- This company: a body", "shown")


def test_a_name_the_study_does_not_cover_still_gets_its_sentence(tmp_path, monkeypatch):
    text = rd.not_shown(dp.SECTOR_NOT_COVERED)
    result, _ = _enrich(tmp_path, monkeypatch, enabled=True, section=(text, dp.SECTOR_NOT_COVERED))
    assert f"## {rd.HEADING}\n{text}\n" in Path(result.brief_path).read_text()


def test_the_model_never_sees_the_section():
    # assess.py builds both prompts. It does not know the section exists, so the prompts are
    # byte-identical with the flag on and off. Giving the line to the model is a separate
    # decision (docs/audits/2026-10-08-moat-durability-phase1.md).
    source = inspect.getsource(assess_mod)
    for name in ("durability_line", "durability_status", "fetch_section", "brief_section",
                 "ROIC persistence", "durability_profile"):
        assert name not in source, name
    assert not hasattr(assess_mod, "durability")
    body = rd.brief_section(_profile(), TABLE)
    bundle = _bundle()
    assert all(body not in text and "ROIC persistence" not in text for _, text in bundle.segments())
    assert "durability" not in " ".join(inspect.signature(assess_mod._build_user_prompt).parameters)


def test_the_shipped_config_has_the_section_off_and_the_studys_sector_mask():
    import hashlib

    root = Path(__file__).resolve().parents[2]
    config = load_config(str(root / "config.yaml"))
    assert config["research"]["durability"] == {
        "enabled": False, "max_table_gap_years": 1, "cache_dir": ".cache/durability-live", "deadline_s": 15}
    # The live sector mask must be the one the study's gates ran on.
    gates = json.loads((root / "docs/audits/raw-2026-10-04-durability/gates.json").read_text())
    now = hashlib.sha256(json.dumps(config["sectors"]["buckets"], sort_keys=True).encode()).hexdigest()
    assert now == gates["sectors_config_sha256"]


def test_fetch_section_swallows_anything(env, monkeypatch, capsys):
    for boom in (KeyError("x"), ZeroDivisionError(), RecursionError(), MemoryError()):
        monkeypatch.setattr(rd, "_section", lambda *a, _b=boom: (_ for _ in ()).throw(_b))
        assert rd.fetch_section(_card(), _bundle(), env.config) == (
            rd.not_shown(dp.UNAVAILABLE), dp.UNAVAILABLE)
    assert capsys.readouterr().err.count("\n") == 4


# ---------------------------------------------------------------- the cache key

def test_the_persistence_section_is_in_the_fingerprint(monkeypatch):
    assert "durability" in cachekey._PROMPT_MODULES
    before = cachekey._prompt_fingerprint()
    monkeypatch.setattr(dp, "table_source", lambda: dp._TABLE_PATH.read_text() + " ")
    assert cachekey._prompt_fingerprint() != before


def test_a_table_that_cannot_be_read_does_not_cost_the_fingerprint(monkeypatch):
    monkeypatch.setattr(dp, "_TABLE_PATH", Path("/nonexistent/durability_table.json"))
    assert dp.table_source() == "" and dp.load_table() is None
    assert cachekey._prompt_fingerprint() != cachekey._FINGERPRINT_FALLBACK
