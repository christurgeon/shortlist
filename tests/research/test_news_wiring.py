"""How the "Recent news" section reaches the brief: after the model call, never in the prompt,
recorded in the brief JSON, and hashed with the brief's wording."""
from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from shortlist import research
from shortlist.config import load_config
from shortlist.research import assess as assess_mod
from shortlist.research import cachekey, capital_allocation, news, report
from shortlist.research.models import FilingBundle, FilingText, Moat, QualitativeAssessment, Thesis

_TS = int(datetime(2026, 10, 15, 12, tzinfo=timezone.utc).timestamp())
ARTICLES = [{"id": 1, "datetime": _TS, "source": "Benzinga", "url": "https://x.test/1",
             "headline": "Acme sued by FTC over subscription billing", "summary": "The FTC alleges."}]



def _assessment():
    return QualitativeAssessment(ticker="ACME", as_of="t", filing_accession="acc-1",
                                 filing_date="2026-02-19", model="m", moat=Moat(),
                                 thesis=Thesis(takeaway="read."), cache_key="acc-1")


def _bundle():
    tenk = FilingText("ACME", "acc-1", "2026-02-19", business="b")
    return FilingBundle(tenk=tenk, primary_accession="acc-1", cache_key="acc-1",
                        filing_date="2026-02-19")


def _enrich(tmp_path, monkeypatch, *, enabled):
    calls = []

    def fake_assess(card, bundle, config, **kw):
        calls.append("assess")
        return _assessment()

    def fake_section(card, config):
        calls.append("news")
        return news.NewsSection(news.select(ARTICLES, "ACME", "Acme Inc.", 10), news.SHOWN,
                                "2026-10-16 14:02 UTC", "2026-09-16", False)

    monkeypatch.setattr(news, "fetch_section", fake_section)
    card = SimpleNamespace(ticker="ACME", composite=90.0, confidence=1.0, scored=True, gates=[],
                           passed=True, metrics=SimpleNamespace(name="Acme Inc.", sic="3674"))
    config = {"research": {"output_root": str(tmp_path), "news": {"enabled": enabled}}}
    (result,) = research.enrich([card], config, top_n=1, fetch=lambda t, **k: _bundle(),
                                assess_fn=fake_assess)
    return result, calls


def test_the_section_is_off_unless_enabled_and_the_brief_is_then_unchanged(tmp_path, monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch, enabled=False)
    assert calls == ["assess"]
    brief = Path(result.brief_path).read_text()
    no_data = (capital_allocation.NOT_SHOWN_NO_DATA, capital_allocation.NOT_SHOWN)
    assert brief == report.to_markdown(_assessment(), capital_allocation=no_data)
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert (record["news_items"], record["news_status"], record["news_as_of"],
            record["news_from"], record["news_truncated"]) == ([], "", "", "", False)


def test_the_section_is_fetched_after_the_model_call_and_recorded(tmp_path, monkeypatch):
    result, calls = _enrich(tmp_path, monkeypatch, enabled=True)
    assert calls == ["assess", "news"]
    brief = Path(result.brief_path).read_text()
    assert f"## {news.HEADING}" in brief and "Acme sued by FTC over subscription billing" in brief
    assert brief.index("## Reconciliation") < brief.index(news.HEADING) < brief.index("## Moat\n")
    record = json.loads(Path(result.brief_path).with_suffix(".json").read_text())
    assert (record["news_status"], record["news_from"]) == (news.SHOWN, "2026-09-16")
    assert record["news_items"][0]["headline"] == "Acme sued by FTC over subscription billing"


def test_the_model_never_sees_the_section():
    source = inspect.getsource(assess_mod)
    for name in ("news_items", "news_status", "fetch_section", "research.news", "company-news"):
        assert name not in source, name
    assert "news" not in " ".join(inspect.signature(assess_mod._build_user_prompt).parameters)


def test_the_section_wording_is_in_the_cache_fingerprint():
    assert "news" in cachekey._PROMPT_MODULES


def test_the_shipped_config_has_the_section_on():
    root = Path(__file__).resolve().parents[2]
    config = load_config(str(root / "config.yaml"))
    assert config["research"]["news"] == {"enabled": True, "lookback_days": 30, "max_items": 10,
                                          "timeout": 10}


def test_the_demo_path_never_fetches_news(monkeypatch, capsys):
    from shortlist.screen import main

    def _no(*a, **k):
        raise AssertionError("the demo must not fetch news")
    monkeypatch.setattr(news, "fetch_articles", _no)
    assert main(["--demo", "--json"]) == 0
