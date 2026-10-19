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
    head = "Acme\u202e sued\u200b by\nDOJ " + "x" * 300
    (item,) = news.select([_art(head, summary="ｓｕｍｍａｒｙ\x07 " + "y" * 400,
                                url="javascript:alert(1)")], "ACME", "Acme Inc.", 10)
    assert "\u202e" not in item.headline and "\u200b" not in item.headline and "\n" not in item.headline
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


def test_feed_html_entities_are_decoded_once_and_still_sanitized():
    (item,) = news.select([_art("Acme sued by FTC &amp; DOJ over Acme&#39;s billing&#8238;",
                                summary="Today&#39;s filing &lt;b&gt;", source="Yahoo&amp;Co")],
                          "ACME", "Acme Inc.", 10)
    assert item.headline == "Acme sued by FTC & DOJ over Acme's billing"
    assert item.summary == "Today's filing <b>"
    assert item.source == "Yahoo&Co"


def test_items_are_shown_newest_first_by_the_date_they_print():
    # The recall cluster starts first (day 0) but its last report (day 3) is the newest.
    arts = [_art("Acme Recall Of Widget Catheters Widens", day=0),
            _art("Acme sued by DOJ over pricing", day=2),
            _art("Acme Widget Catheters Recall Classified Class I", day=3)]
    items = news.select(arts, "ACME", "Acme Inc.", 10)
    assert [(i.date, i.reports) for i in items] == [("2026-10-03", 1), ("2026-10-01", 2)]


def test_an_entity_encoded_company_name_still_matches():
    (item,) = news.select([_art("AT&amp;T to acquire Lumen fiber unit")], "T", "AT&T Inc.", 10)
    assert item.headline == "AT&T to acquire Lumen fiber unit"


@pytest.mark.parametrize("headline", [
    "Acme Reports Earnings Tomorrow: What To Expect",
    "Acme Stock Heads into Earnings with Cloud Growth in Focus",
    "Acme Announces Conference Call to Review Third Quarter Results",
    "Acme Will Announce Third Quarter 2026 Earnings Results on November 4",
    "Transcript: Acme Q3 2026 Earnings Conference Call",
    "Acme (ACME) Upgraded to Strong Buy: Here's Why",
    "Acme Gets Slew Of Price Target Hikes Ahead Of Earnings",
    "Acme Just Raised Its Outlook. Here's Why the Dividend King Is Still a Buy.",
    "Better Dividend Stock for Retirees: Beta vs. Acme",
    "Here's How Much Traders See Acme Stock Moving After Earnings",
])
def test_previews_notices_transcripts_and_columns_are_not_events(headline):
    assert news.select([_art(headline)], "ACME", "Acme Inc.", 10) == []


def test_a_broker_rating_change_stays_an_event():
    head = "Leerink Partners Downgrades Acme to Market Perform, Lowers Price Target to $59"
    assert _heads(news.select([_art(head)], "ACME", "Acme Inc.", 10)) == [head]


def test_a_strong_event_outranks_a_more_syndicated_weak_one():
    arts = [_art("Acme Reports Third Quarter Results", day=1, id_=500 + k) for k in range(5)]
    arts.append(_art("Acme sued by DOJ over pricing", day=2))
    items = news.select(arts, "ACME", "Acme Inc.", 1)
    assert _heads(items) == ["Acme sued by DOJ over pricing"]


def test_a_dividend_action_is_an_event():
    head = "Acme Raises Quarterly Dividend by 8%"
    assert _heads(news.select([_art(head)], "ACME", "Acme Inc.", 10)) == [head]


# ---- one item per event: the merge pass after the seed clustering ----
_OTHER = [_art("Acme sued by DOJ over pricing", day=1, summary="antitrust complaint filed"),
          _art("Acme wins Navy contract", day=2, summary="ship maintenance award"),
          _art("Acme opens new plant in Ohio", day=3, summary="factory capacity jobs")]


def _reports(items, needle):
    return [i.reports for i in items if needle in i.headline]


def test_differently_worded_coverage_of_one_event_is_one_item():
    arts = _OTHER + [
        _art("Acme recalls widget catheters", day=0, summary="widget catheter recall over tip failures"),
        _art("Acme slides as widget catheter recall widens", day=1, summary="the widget catheter recall widens"),
        _art("Acme expands catheter recall", day=2, summary="more widget catheters recalled")]
    items = news.select(arts, "ACME", "Acme Inc.", 10)
    assert len([i for i in items if "atheter" in i.headline]) == 1
    assert sum(_reports(items, "atheter")) == 3


def test_one_quarter_is_one_item_and_separate_months_stay_separate():
    q3 = [_art("Acme reports third quarter results", day=0),
          _art("Acme beats estimates as quarter revenue rises", day=1),
          _art("Acme Q3 earnings top views", day=1),
          _art("Acme lifts guidance after strong quarter", day=2)]
    assert len(news.select(_OTHER + q3, "ACME", "Acme Inc.", 10)) == len(_OTHER) + 1
    months = [_art("Acme reports August results", day=0), _art("Acme reports September results", day=26)]
    assert len(news.select(months, "ACME", "Acme Inc.", 10)) == 2


def test_a_deal_story_merges_across_a_week_only_with_a_shared_counterparty():
    talks = _art("Beta in merger talks with Acme", day=0, summary="talks at an early stage")
    later = [_art("Beta explored takeover of Acme", day=5, summary="a possible megadeal")]
    assert len(news.select([talks] + later, "ACME", "Acme Inc.", 10)) == 1
    other = [_art("Gamma explored takeover of Acme", day=5, summary="a possible megadeal")]
    assert len(news.select([talks] + other, "ACME", "Acme Inc.", 10)) == 2
    late = [_art("Beta explored takeover of Acme", day=8, summary="a possible megadeal")]
    assert len(news.select([talks] + late, "ACME", "Acme Inc.", 10)) == 2


def test_a_denial_is_never_folded_into_the_report_it_answers():
    arts = [_art("Beta explored takeover of Acme", day=0, summary="beta weighed a bid for acme"),
            _art("Beta denies plan to buy Acme after takeover report", day=1, summary="beta weighed no bid for acme")]
    assert len(news.select(arts, "ACME", "Acme Inc.", 10)) == 2


def test_a_merge_never_chains_so_a_story_cannot_snowball():
    arts = _OTHER + [_art("Acme sued over alpha pumps", day=0, summary="alpha pumps bravo valves"),
                     _art("Acme recall covers bravo valves", day=1, summary="bravo valves charlie hoses"),
                     _art("Acme wins charlie hose contract", day=2, summary="charlie hoses delta seals")]
    items = news.select(arts, "ACME", "Acme Inc.", 10)
    assert _reports(items, "alpha") + _reports(items, "bravo") == [2]
    assert _reports(items, "charlie") == [1]


def test_two_trial_readouts_in_one_week_stay_two_items():
    arts = _OTHER + [
        _art("Acme drug zeta meets primary endpoint in phase 3 psoriasis trial", day=0,
             summary="zeta cleared skin in most patients"),
        _art("Acme phase 3 lymphoma trial of eta succeeds", day=2,
             summary="eta extended survival in first-line lymphoma")]
    assert len(news.select(arts, "ACME", "Acme Inc.", 10)) == len(_OTHER) + 2


def test_the_section_does_not_depend_on_feed_order():
    import random
    arts = _OTHER + [_art("Acme recalls widget catheters", day=0, summary="widget catheter recall"),
                     _art("Acme widget catheter recall widens", day=0, summary="widget catheter recall")]
    want = news.select(arts, "ACME", "Acme Inc.", 10)
    for seed in range(5):
        shuffled = arts[:]
        random.Random(seed).shuffle(shuffled)
        assert news.select(shuffled, "ACME", "Acme Inc.", 10) == want


@pytest.mark.parametrize("pair", [
    ("Acme Q3 earnings: CEO flags cost cuts", "Acme reports positive phase 3 trial results"),
    ("Acme Q3 results top estimates", "Acme raises quarterly dividend by 7%"),
    ("Acme announces layoffs across its chip unit", "Acme expands Ohio workforce at new plant"),
])
def test_an_event_that_rides_along_with_results_or_layoffs_keeps_its_own_item(pair):
    arts = _OTHER + [_art(pair[0], day=0, summary="first story"),
                     _art(pair[1], day=1, summary="second story")]
    assert len(news.select(arts, "ACME", "Acme Inc.", 10)) == len(_OTHER) + 2


def test_a_deal_merge_needs_a_real_counterparty_not_a_weekday_or_a_rating():
    weekday = [_art("Acme to acquire Beta's delivery unit", day=0, summary="On Monday, the deal"),
               _art("Acme bids for Gamma stake", day=6, summary="On Monday, a bid")]
    assert len(news.select(weekday, "ACME", "Acme Inc.", 10)) == 2
    rating = [_art("Goldman Sachs upgrades Acme to Buy", day=0, summary="rating change"),
              _art("Acme to acquire Delta from Goldman Sachs fund", day=5, summary="a purchase")]
    assert len(news.select(rating, "ACME", "Acme Inc.", 10)) == 2
