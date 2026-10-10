"""The "ROIC persistence" section: what it says, what it must always say, and that fetching it
can never cost a brief."""
import inspect
import itertools
import json
import sys
import time
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
                 "above 15.5%, the top-fifth cutoff among the 1,787 firms in the study's fiscal-2025 universe",
                 "59% of top-fifth firms (cohorts formed 2011-2017) and 54% (2018-2021)",
                 "The 16% and 11% that did not",
                 "invested capital +34.0% in the last year, the fastest-growing third",
                 "(slowest third: +2.5% or less; fastest third: over +14.5%; all sectors pooled, not "
                 "sector-adjusted)",
                 "15 percentage points more likely (95% interval 6 to 24) than one at the fastest-growing extreme",
                 "in the 2011-2017 cohorts, and 20 points (9 to 31) in 2018-2021",
                 "(54%, 59%) runs through capital that kept growing slowly",
                 "over its last 4 years its ROIC percentile rank in the study's universe had a standard "
                 "deviation of 2.1 percentile points, the steadiest third",
                 "(steadiest third: 3.1 or less; least steady third: over 9.1)",
                 "15 percentage points more likely (95% interval 3 to 26) than one at the least steady extreme",
                 # `stability` has no 2011 cohort: its first window is 2012-2017, not 2011-2017.
                 "in the 2012-2017 cohorts, and 22 points (9 to 35) in 2018-2021",
                 "Eleven tests were run on six predictors. Three cleared the 2011-2017 cohorts; two of "
                 "them (capital growth and steadiness) also cleared the 2018-2021 cohorts"):
        assert want in text, want
    # The four by-cohort-year figures are in the verdict and NOT here: +62 is the number a
    # hurried reader would keep. The sentence that the result rests on two years stays.
    assert "+62" not in text and "+35" not in text


def test_three_years_of_history_are_said_as_three():
    line = next(ln for ln in rd.brief_section(_profile(years_seen=3), TABLE).split("\n")
                if ln.startswith("- ROIC steadiness"))
    assert "over the 3 of its last 4 years that have a ROIC its ROIC percentile rank" in line


def test_the_printed_cut_points_and_the_label_cannot_disagree():
    # One definition for both (durability_profile.cuts). On the real table, for every capital
    # growth from -60% to +60% and every rank spread from 0 to 30 points, the third the profile
    # assigns is the one a reader would work out from the cut values printed beside it.
    slow, fast = dp.cut_points(TABLE)["investment"]
    for i in range(-2400, 2401):
        g = i / 4000
        want = 2 if g <= slow else (0 if g > fast else 1)
        assert dp.third(-g, TABLE["cohort"]["investment"]) == want, g
    steady, unsteady = dp.cut_points(TABLE)["stability"]
    for i in range(3001):
        spread = i / 10000
        want = 2 if spread <= steady else (0 if spread > unsteady else 1)
        assert dp.third(-spread, TABLE["cohort"]["stability"]) == want, spread
    assert (rd._signed_pct(slow, 1), rd._signed_pct(fast, 1)) == ("+2.5%", "+14.5%")


def test_the_caveats_the_verdict_requires_are_always_there():
    text = rd.brief_section(_profile(), TABLE)
    lines = text.split("\n")
    assert lines[0] == ("A historical pattern in an accounting ratio, not a forecast for this "
                        "company. It says nothing about moat, price or returns.")
    growth = next(ln for ln in lines if ln.startswith("- Capital growth:"))
    for clause in ("all sectors pooled, not sector-adjusted",
                   "same two-digit SIC sector and cohort year, with ROIC rank and revenue rank held fixed",
                   "These are the two ends of a straight-line fit, not a gap between thirds",
                   "Among firms still profitable three years later, a little over half of the link",
                   "positive but not distinguishable from zero in 2018-2021",
                   "This split is descriptive",
                   "Not tested: acquired capital, cash and payouts (buybacks lower invested capital), "
                   "a one-year profit spike",
                   "a pattern in the ratio and in capital growth, not a finding about the business"):
        assert clause in growth, clause
    steady = next(ln for ln in lines if ln.startswith("- ROIC steadiness"))
    for clause in ("a track-record line: a longer view of the same ROIC level, not evidence of a separate trait",
                   "With the same controls,",
                   "The 2018-2021 result comes from the 2020 and 2021 cohorts; in 2018 and 2019 the "
                   "effect went the other way"):
        assert clause in steady, clause
    cohorts = next(ln for ln in lines if ln.startswith("- Past cohorts:"))
    for clause in ("were again at or above the top-fifth cutoff three years later",
                   "Only firms that still filed usable 10-K data then are counted",
                   "(stopped filing, for example after a takeover or a failure; filed late; or left an "
                   "input untagged) are left out",
                   "A few counted firms, under 4%, had too little invested capital for a ROIC by then "
                   "and count as held because their operating income was positive",
                   "a group average, not adjusted for this company's ROIC level"):
        assert clause in cohorts, clause
    # The pass rule includes the later cohorts: never "two passed and were then checked".
    assert lines[-1].endswith("which share firms with the earlier ones, so the second stage is not "
                              "an independent sample.")
    assert "then checked" not in text
    # One unit per number: an effect is in percentage points, a rank spread in percentile points.
    assert " points more often" not in text and "varied by" not in text


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
    for clause in ("no cutoff for this company's fiscal year", "within 1 percentage point", "book equity"):
        assert clause not in plain
    assert ("The study has no cutoff for this company's fiscal year yet, so the fiscal-2025 cutoff is "
            "used.") in rd.brief_section(_profile(bucket=2026, period_end="2026-12-31"), TABLE)
    floor = TABLE["floors"][2025]
    assert "It is within 1 percentage point of the cutoff." in rd.brief_section(
        _profile(roic=floor + 0.0099), TABLE)
    assert "within 1 percentage point" not in rd.brief_section(_profile(roic=floor + 0.0101), TABLE)
    assert ("No debt is reported in the tagged data, so invested capital here is book equity."
            in rd.brief_section(_profile(has_debt=False), TABLE))


@pytest.mark.parametrize("growth, want", [(0.0004, "invested capital 0.0% in"), (-0.0004, "invested capital 0.0% in"),
                                          (-0.105, "invested capital -10.5% in"), (0.125, "invested capital +12.5% in"),
                                          (15.39, "invested capital +1539.0% in")])
def test_capital_growth_never_prints_minus_zero(growth, want):
    assert want in rd.brief_section(_profile(capital_growth=growth), TABLE)


def test_rounding_is_half_up_on_the_printed_decimal():
    assert [str(rd._round(x)) for x in (0.5, 1.5, 2.5, -0.5, -0.3, 0.0)] == ["1", "2", "3", "-1", "0", "0"]
    assert rd._pct(0.155325, 1) == "15.5%" and rd._pct(0.585) == "59%" and rd._pct(0.5849) == "58%"
    assert rd._signed_points(-0.0369) == "-4" and rd._signed_points(0.3466) == "+35"


def test_every_reason_has_one_sentence_and_no_other():
    assert set(rd._REASONS) == set(dp.NOT_SHOWN)
    detail = {"roic": 0.091, "floor": 0.1553, "period_end": "2025-12-31", "table_year": 2025}
    data_limits = {dp.STALE, dp.REFERENCE_OUT_OF_DATE, dp.BEFORE_REFERENCE, dp.FACTS_LAG_FILING,
                   dp.UNAVAILABLE}
    for reason in dp.NOT_SHOWN:
        text = rd.not_shown(reason, detail)
        assert text.startswith("Not shown: ") and "{" not in text and "moat" not in text.lower()
        # A name the study does not cover is told what it covers. A name whose DATA could not be
        # used is told that, and never that it is "not covered".
        if reason in data_limits:
            assert text.endswith("This is a limit of the data, not a reading of the company.")
            assert "The study covers only" not in text
        else:
            assert text.endswith("Absence says nothing about this company.")
        assert "buyback" not in text            # one cause among several, and a judgment
    assert ("ROIC 9.1% for the year ended 2025-12-31 (the study's basis) is below 15.5%, the top-fifth "
            "cutoff of fiscal 2025") in rd.not_shown(dp.NOT_TOP_FIFTH, detail)
    # Never "15.5% is below 15.5%": a second decimal when one cannot tell the two apart.
    close = rd.not_shown(dp.NOT_TOP_FIFTH, {**detail, "roic": 0.15512, "floor": 0.15533})
    assert "ROIC 15.51% for the year ended" in close and "is below 15.53%, the top-fifth cutoff" in close
    # ... and a third or a fourth when two cannot: the table's own cutoff is 15.5325%.
    closer = rd.not_shown(dp.NOT_TOP_FIFTH, {**detail, "roic": 0.15531, "floor": 0.155325})
    assert "ROIC 15.531% for the year ended" in closer and "is below 15.532%, the top-fifth cutoff" in closer
    for roic in (0.1553, 0.15526, 0.1553249, 0.155324999):
        text = rd.not_shown(dp.NOT_TOP_FIFTH, {**detail, "roic": roic, "floor": 0.155325})
        shown, cutoff = text.split("ROIC ")[1].split("%")[0], text.split("is below ")[1].split("%")[0]
        assert float(shown) < float(cutoff), text
    assert ("its latest year on file ended 2025-12-31, which is before the year the reference table is "
            "built for (fiscal 2025); the section needs its newer 10-K") in rd.not_shown(dp.BEFORE_REFERENCE, detail)
    assert len(dp.NOT_SHOWN) == 13


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
    net = _Net(_json(body))
    assert _fetch(env, net)[1] == dp.UNAVAILABLE
    assert capsys.readouterr().err.count("\n") == 1
    # A record that `profile` could not read is NOT cached: the next brief asks again, so a
    # body the SEC corrects the same day is picked up.
    assert not env.cache.exists() or not list(env.cache.iterdir())
    assert _fetch(env, net)[1] == dp.UNAVAILABLE and len(net.requests) == 2


def test_an_orphan_temp_file_from_a_dead_process_is_pruned_with_the_old_days(env):
    env.cache.mkdir()
    orphan = env.cache / "CIK0000009999-2026-03-01.json.123.456.tmp"
    recent = env.cache / "CIK0000009999-2026-03-14.json.123.456.tmp"
    other = env.cache / "notes.txt"
    for f in (orphan, recent, other):
        f.write_text("x")
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.SHOWN
    assert sorted(p.name for p in env.cache.iterdir()) == [
        "CIK0000001234-2026-03-15.json", recent.name, "notes.txt"]


@pytest.mark.parametrize("knobs", [{"max_table_gap_years": None, "deadline_s": None},
                                   {"max_table_gap_years": "one", "deadline_s": "soon"},
                                   {"max_table_gap_years": True, "deadline_s": -5},
                                   {"max_table_gap_years": float("inf"), "deadline_s": float("inf")},
                                   {"max_table_gap_years": float("nan"), "deadline_s": 1e12},
                                   {"max_table_gap_years": 10 ** 400, "deadline_s": 10 ** 400}])
def test_a_null_or_mistyped_knob_falls_back_to_its_default(env, knobs):
    env.config["research"]["durability"].update(knobs)
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.SHOWN


def test_a_deadline_cannot_be_configured_past_the_slack_of_the_research_phase():
    # `thread.join(inf)` raises, and a deadline of ten minutes is the budget of the whole phase.
    assert rd._number({"deadline_s": 600}, "deadline_s", 15.0, most=rd.MAX_DEADLINE_S) == 45.0
    assert rd._number({"deadline_s": 20}, "deadline_s", 15.0, most=rd.MAX_DEADLINE_S) == 20.0
    assert rd._number({"deadline_s": 0}, "deadline_s", 15.0, most=rd.MAX_DEADLINE_S) == 15.0


def test_a_table_gap_of_zero_is_a_setting_and_not_a_mistake(env):
    # Zero means "the table's own year only". Read as the default, a company one fiscal year
    # past the table would be ranked against it with nothing to say the knob was ignored.
    today = date(2027, 2, 20)
    body = _body({y + 1: v for y, v in FOUR_YEARS.items()})
    bundle = _bundle(filed="2027-02-20")
    assert _fetch(env, _Net(_json(body)), bundle=bundle, today=today)[1] == dp.SHOWN
    for zero in (0, 0.0):
        env.config["research"]["durability"]["max_table_gap_years"] = zero
        assert _fetch(env, _Net(_json(body)), bundle=bundle,
                      today=today + timedelta(days=1))[1] == dp.REFERENCE_OUT_OF_DATE


@pytest.mark.parametrize("cache_dir", [123, ["a"], "", None])
def test_a_cache_directory_that_is_not_a_path_is_the_default_one(env, cache_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)                     # the default is relative to the working directory
    env.config["research"]["durability"]["cache_dir"] = cache_dir
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.SHOWN
    assert [p.name for p in (tmp_path / rd.DEFAULT_CACHE_DIR).iterdir()] == ["CIK0000001234-2026-03-15.json"]


def test_a_filing_date_that_is_not_a_date_is_not_a_lag(env):
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))), bundle=_bundle(filed="n/a"))[1] == dp.SHOWN


def test_a_failed_cache_write_leaves_no_temp_file(env, monkeypatch):
    monkeypatch.setattr(rd.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.SHOWN
    assert list(env.cache.iterdir()) == []


def test_a_closed_stderr_does_not_cost_the_brief(env, monkeypatch):
    class Closed:
        def write(self, _):
            raise ValueError("I/O operation on closed file")

    monkeypatch.setattr(rd.sys, "stderr", Closed())
    assert _fetch(env, _Net(httpx.Response(403)))[1] == dp.UNAVAILABLE


@pytest.mark.parametrize("bad", [True, "on", 1, ["enabled"], None])
def test_a_config_block_of_the_wrong_type_is_off_and_loses_nothing(tmp_path, monkeypatch, bad):
    monkeypatch.setattr(rd, "fetch_section", lambda *a: pytest.fail("the section is off"))
    config = {"research": {"output_root": str(tmp_path), "durability": bad}}
    (result,) = research.enrich([_rank_card()], config, top_n=1, fetch=lambda t, **k: _bundle(),
                                assess_fn=lambda *a, **k: _assessment())
    assert result.brief_path and not result.skipped


def test_a_wrong_type_inside_the_section_config_falls_back_to_the_defaults(env, monkeypatch):
    monkeypatch.setattr(rd, "DEFAULT_CACHE_DIR", str(env.cache))
    env.config["research"]["durability"] = True
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.SHOWN
    assert [p.name for p in env.cache.iterdir()] == ["CIK0000001234-2026-03-15.json"]


def test_the_cik_lookup_does_not_reset_an_identity_that_is_set(monkeypatch):
    # edgartools' set_identity closes the HTTP client all its callers share. The section runs
    # after a model call, while another brief's thread may be fetching its filing.
    from shortlist.research import filings

    calls = []
    monkeypatch.setitem(sys.modules, "edgar",
                        SimpleNamespace(Company=lambda ticker: SimpleNamespace(cik="1234")))
    monkeypatch.setattr(filings, "require_identity", lambda: calls.append("set_identity"))
    monkeypatch.setenv("EDGAR_IDENTITY", "tester@example.com")
    assert rd._cik("TEST") == 1234 and calls == []
    monkeypatch.delenv("EDGAR_IDENTITY")
    assert rd._cik("TEST") == 1234 and calls == ["set_identity"]


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


def test_the_deadline_bounds_the_whole_request_whatever_the_server_does(env):
    # An httpx timeout is per phase and per read, so a server that keeps a read alive is never
    # timed out. The caller waits `deadline_s` for the worker thread and no longer.
    env.config["research"]["durability"]["deadline_s"] = 0.2

    def stall(request):
        time.sleep(1.5)
        return _json(_body(FOUR_YEARS))

    start = time.monotonic()
    assert _fetch(env, _Net(stall))[1] == dp.UNAVAILABLE
    assert time.monotonic() - start < 1.0
    assert not env.cache.exists() or not list(env.cache.iterdir())


def test_a_body_that_arrives_after_the_deadline_is_dropped(env, monkeypatch):
    clock = itertools.chain([0.0], itertools.repeat(16.0))
    monkeypatch.setattr(rd.time, "monotonic", lambda: next(clock))
    assert _fetch(env, _Net(_json(_body(FOUR_YEARS))))[1] == dp.UNAVAILABLE


def test_an_error_in_the_worker_thread_reaches_the_guard(env, capsys):
    def boom(request):
        raise httpx.ConnectError("no route to host")

    assert _fetch(env, _Net(boom))[1] == dp.UNAVAILABLE
    assert "ConnectError" in capsys.readouterr().err


def test_a_bank_gets_its_reason_without_a_request(env, monkeypatch):
    monkeypatch.setattr(rd, "_cik", lambda t: pytest.fail("no CIK lookup for a masked sector"))
    net = _Net(_json(_body(FOUR_YEARS)))
    assert _fetch(env, net, card=_card(sic="6021")) == (
        rd.not_shown(dp.SECTOR_NOT_COVERED), dp.SECTOR_NOT_COVERED)
    assert _fetch(env, net, card=_card(sic=None))[1] == dp.SIC_UNKNOWN
    assert _fetch(env, net, card=SimpleNamespace(ticker="X"))[1] == dp.SIC_UNKNOWN     # no metrics
    assert net.requests == []


def test_no_table_is_unavailable_without_a_request(env, monkeypatch, capsys):
    monkeypatch.setattr(rd, "load_table", lambda: None)
    net = _Net(_json(_body(FOUR_YEARS)))
    assert _fetch(env, net)[1] == dp.UNAVAILABLE and net.requests == []
    # Not silent: in a build without the table EVERY brief reads "could not be read".
    assert "ROIC-persistence section failed for TEST: RuntimeError: the reference table" in capsys.readouterr().err


def test_a_company_the_study_does_not_cover_gets_its_reason(env):
    low = {**FOUR_YEARS, 2025: (50.0, 400.0)}                   # a ROIC of 9.9%
    text, status = _fetch(env, _Net(_json(_body(low))))
    assert status == dp.NOT_TOP_FIFTH and "ROIC 9.9% for the year ended 2025-12-31" in text
    assert "the top-fifth cutoff of fiscal 2025" in text
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
    assert len(net.requests) == 1                   # a download made now is not made again
    assert _fetch(env, net, bundle=_bundle(filed="2027-02-20"), today=today)[1] == dp.FACTS_LAG_FILING
    assert len(net.requests) == 2                   # from the day cache, then once past it


def test_a_brief_makes_at_most_one_request(env):
    # The deadline bounds one request, so the section's worst case is one deadline.
    today = date(2027, 2, 20)
    net = _Net(_json(_body(FOUR_YEARS)))
    for _ in range(3):                              # cold, then twice on a cache that lags
        before = len(net.requests)
        _fetch(env, net, bundle=_bundle(filed="2027-02-20"), today=today)
        assert len(net.requests) - before == 1
    before = len(net.requests)
    _fetch(env, net, bundle=_bundle(filed="2026-02-19"), today=today)       # no lag: the cache serves
    assert len(net.requests) == before


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
