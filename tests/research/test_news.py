"""The "Recent news" filter: which company-news articles become event clusters in the brief.

Rules and evidence: docs/audits/2026-10-16-deep-news-leads-design.md §4.2.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shortlist.research import news
from shortlist.research.models import NewsItem

_T0 = int(datetime(2026, 10, 1, 12, tzinfo=timezone.utc).timestamp())
_DAY = 86400


def _art(headline, *, day=0, source="Yahoo", summary="", url="https://example.com/a", id_=None):
    return {"id": id_ or abs(hash((headline, day))), "datetime": _T0 + day * _DAY,
            "headline": headline, "summary": summary, "source": source, "url": url}


def _heads(items):
    return [i.headline for i in items]


# ---- who the article is about ----

@pytest.mark.parametrize("ticker,name,headline,about", [
    ("ALL", "The Allstate Corporation", "ALL eyes on Fed as tech lawsuit fears rise", False),
    ("NOW", "ServiceNow, Inc.", "Shares NOW trading higher after Nvidia deal", False),
    ("ON", "ON Semiconductor Corporation", "Fed turns on lawsuit crackdown, ON strikes deal", False),
    ("ON", "ON Semiconductor Corporation", "ON Semiconductor (ON) cuts guidance", True),
    ("LMT", "Lockheed Martin Corporation", "LMT Stock Tracks Best Week", True),
    ("BMY", "Bristol-Myers Squibb Company", "Bristol Myers Squibb Reports Mixed Phase III Results", True),
    ("CALM", "Cal-Maine Foods, Inc.", "Cal Maine Foods Swings to Loss", True),
    ("BA", "The Boeing Company", "Boeing machinists vote to strike", True),
    ("SMCI", "Super Micro Computer, Inc.", "SMCI taps former Western Digital CFO as new finance boss", True),
    ("UBER", None, "Uber to Acquire ezCater for $2.3 Billion", False),      # no name: ticker only
])
def test_an_article_is_about_the_company_only_when_its_headline_says_so(ticker, name, headline, about):
    assert any(p.search(headline) for p in news.aliases(ticker, name)) is about


# ---- what counts as an event ----

@pytest.mark.parametrize("headline", [
    "Acme restates three years of revenue", "Acme auditor raises going concern doubt",
    "Hindenburg short seller report targets Acme", "Acme files for Chapter 11 bankruptcy",
    "Nasdaq sends Acme delisting notice", "Acme discloses material weakness",
    "Acme withdraws guidance", "Trading halted in Acme shares", "Acme accused of fraud by SEC",
    "Acme defaults on notes", "Why Is Acme Tightening Fraud Controls Across Its Network?",
])
def test_distress_headlines_are_strong_events_even_in_question_form(headline):
    assert news.grade(headline) == 2


def test_a_valuation_piece_is_not_an_event_and_a_factual_result_is():
    assert news.grade("Is Acme Stock a Buy at 10 Times Next Year's Earnings Guidance?") == 0
    assert news.grade("Acme Swings to Fiscal First-Quarter Loss Amid Sales Drop") == 1


def test_roundups_are_dropped_and_title_case_prose_with_commas_is_not_a_roundup():
    roundup = _art("Power Integrations, Penguin Solutions, Acme, Amkor, and Teradyne Shares Rise On Deal")
    prose = _art("Acme Stock: While an Activist Demanded a New CEO, Its Actual CEO Was Buying, "
                 "and So Were Three Other Insiders.", day=1)
    assert _heads(news.select([roundup, prose], "ACME", "Acme Inc.", 10)) == [prose["headline"]]


def test_opinion_sources_and_price_target_notes_are_dropped():
    arts = [_art("Acme: The $60 Billion Warning Investors Ignore", source="SeekingAlpha"),
            _art("Acme sued by FTC over billing", source="ChartMill", day=1),
            _art("Citigroup Maintains Neutral on Acme, Raises Price Target to $45", day=2),
            _art("Barclays Downgrades Acme to Underweight", day=3)]
    assert _heads(news.select(arts, "ACME", "Acme Inc.", 10)) == ["Barclays Downgrades Acme to Underweight"]


# ---- clusters ----

def test_one_story_from_many_outlets_is_one_item_faced_by_its_strongest_headline():
    arts = [_art("Acme Earnings Could Move This Dow ETF: Vineyard Wind Math", day=0),
            _art("Acme and Vineyard Wind settle litigation over Massachusetts project", day=1),
            _art("Acme Gains as Vineyard Wind fight ends in settlement", day=2)]
    (item,) = news.select(arts, "ACME", "Acme Inc.", 10)
    assert item.headline == "Acme and Vineyard Wind settle litigation over Massachusetts project"
    assert item.reports == 3
    assert item.date == "2026-10-01"                 # the first report, not the face's


def test_clusters_match_the_seed_so_a_story_cannot_snowball():
    # Keys are capitalized words and figures; the third shares "Leerink" with the second only.
    arts = [_art("Acme Phase 3 Admilparant Trial Misses At High Dose", day=0),
            _art("Acme Admilparant Phase 3 Dose Mixed, Leerink Notes", day=1),
            _art("Leerink Downgrades Acme To Market Perform On Pipeline Doubt", day=2)]
    assert len(news.select(arts, "ACME", "Acme Inc.", 10)) == 2


def test_a_quiet_name_is_never_padded_with_non_events():
    arts = [_art("Acme Brand Strength and Product Innovation to Bolster Growth"),
            _art("Acme sues Five Below over clog design patent", day=1)]
    assert _heads(news.select(arts, "ACME", "Acme Inc.", 10)) == ["Acme sues Five Below over clog design patent"]


def test_the_cap_keeps_the_most_reported_stories_and_shows_them_newest_first():
    agencies = ["Navy", "Army", "NASA", "Treasury", "Interior"]
    arts = [_art(f"Acme wins {a} contract", day=n) for n, a in enumerate(agencies)]
    arts += [_art("Acme Recall Of Widget Catheters Widens", day=1, id_=900 + k) for k in range(3)]
    items = news.select(arts, "ACME", "Acme Inc.", 3)
    assert items[0].headline == "Acme wins Interior contract"
    assert "Acme Recall Of Widget Catheters Widens" in _heads(items)
    assert [i.date for i in items] == sorted((i.date for i in items), reverse=True)


def test_malformed_rows_are_skipped_not_fatal():
    arts = [None, "x", {"headline": "Acme sued"}, _art("Acme sued by DOJ over pricing")]
    assert _heads(news.select(arts, "ACME", "Acme Inc.", 10)) == ["Acme sued by DOJ over pricing"]


# ---- third-party text is made safe once, when the item is built ----

def test_text_is_normalized_stripped_of_control_characters_and_capped():
    head = "Acme‮ sued​ by\nDOJ " + "x" * 300
    (item,) = news.select([_art(head, summary="ｓｕｍｍａｒｙ\x07 " + "y" * 400,
                                url="javascript:alert(1)")], "ACME", "Acme Inc.", 10)
    assert "‮" not in item.headline and "​" not in item.headline and "\n" not in item.headline
    assert item.headline.startswith("Acme sued by DOJ") and len(item.headline) <= 160
    assert item.summary.startswith("summary ") and len(item.summary) <= 220
    assert item.url == ""


# ---- the brief's markdown ----

def test_markdown_escapes_the_headline_and_the_link():
    item = NewsItem(date="2026-10-15", source="Benzinga", headline="Acme [beats] *estimates*",
                    summary="Shares rose_fast", url="https://x.test/a(b)", reports=3)
    lines = news.markdown_lines([item], news.SHOWN, "2026-10-16 14:02 UTC", "2026-09-16")
    assert lines[:2] == ["", f"## {news.HEADING}"]
    assert "from 2026-09-16 to 2026-10-16 14:02 UTC; dates are UTC" in lines[2]
    assert ("- 2026-10-15 · Benzinga · 3 reports — "
            "[Acme \\[beats\\] \\*estimates\\*](https://x.test/a%28b%29)") in lines
    assert "  Shares rose\\_fast" in lines


def test_markdown_says_unavailable_and_none_differently_and_nothing_when_off():
    down = news.markdown_lines([], news.UNAVAILABLE, "t")
    quiet = news.markdown_lines([], news.NONE, "t")
    assert f"_{news.UNAVAILABLE_TEXT}_" in down and f"_{news.NONE_TEXT}_" in quiet
    assert news.markdown_lines([], "", "t") == []


def test_a_capped_feed_is_said_in_the_note():
    lines = news.markdown_lines([], news.NONE, "t", "2026-10-13", truncated=True)
    assert news.TRUNCATED_TEXT in lines[2] and "from 2026-10-13" in lines[2]


def test_a_summary_cannot_start_a_list_and_tildes_are_escaped():
    for summary, want in [("1. Acme ~~beat~~", "  1\\. Acme \\~\\~beat\\~\\~"),
                          ("- Acme beat", "  \\- Acme beat"), ("+ Acme beat", "  \\+ Acme beat")]:
        item = NewsItem(date="d", source="s", headline="h", summary=summary)
        assert news.markdown_lines([item], news.SHOWN, "t")[-1] == want


def test_a_url_with_a_format_character_is_dropped():
    assert news.safe_url("https://a.test/\u202eexe.fdp") == ""
    assert news.safe_url("https://a.test/ok") == "https://a.test/ok"
