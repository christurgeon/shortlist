"""Recent company news for the research brief: a deterministic, display-only section.

Fetched AFTER the model call and never shown to the model, so the prompt is byte-identical
with the section on or off. Headlines are third-party leads, not filing facts, and the outlet
is often a syndicator. Behaviour: docs/RESEARCH.md → the recent-news section. Why these rules:
docs/audits/2026-10-16-deep-news-leads-design.md §4.2.
"""
from __future__ import annotations

import html
import os
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from .models import NewsItem

URL = "https://finnhub.io/api/v1/company-news"
HEADING = "Recent news — third-party, not verified"
NOTE = ("Finnhub company news from {covers_from} to {as_of}; dates are UTC. Headlines are leads, "
        "not filing facts, and the outlet is often a syndicator.")
TRUNCATED_TEXT = ("The feed returns only its newest articles, so for this company it covers less "
                  "than the configured window.")
NONE_TEXT = "No company-specific event headlines in the window."
UNAVAILABLE_TEXT = ("The news feed was unavailable for this brief. An empty section is not "
                    "evidence of no news.")
SHOWN, NONE, UNAVAILABLE = "shown", "none", "unavailable"

_DEFAULTS = {"enabled": False, "lookback_days": 30, "max_items": 10, "timeout": 10}
# The same research-phase budget as research/durability.py:_download, for the same reason.
MAX_DEADLINE_S = 45.0
# Finnhub's free company-news returns only its newest ~250 articles; a list this long is capped
# (the data/sources/finnhub.py rule).
TRUNCATE_AT = 240
_HEADLINE_MAX = 160
_SUMMARY_MAX = 220
_CLUSTER_SECONDS = 4 * 86400
_MIN_SHARED_KEYS = 2
_REPORTS_CAP = 5            # ranking only: past this, more syndication is not more corroboration

_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|holdings?|group|"
                     r"ltd|plc|n\.?v|s\.?a|class [a-c])\.?$", re.I)
_GENERIC = frozenset({"the", "first", "general", "american", "united", "national",
                      "international", "global", "new", "bank"})
# Contributor opinion and screen output by construction, bullish and bearish alike.
_OPINION_SOURCES = frozenset({"SeekingAlpha", "ChartMill"})
# Never an event. Price-target notes are dropped because the analyst_revision line already
# carries rating changes.
_DROP = re.compile(
    r"^prediction:|\btop \d+\b|\b\d+ (stocks|reasons|things)\b|\bstocks to (watch|buy)\b|"
    r"facts to (know|note)|trending stock|what you need to know|important facts|analyst blog|"
    r"some (facts|information) |things to know|\bearnings preview\b|\bunusual options\b|"
    r"\boptions activity\b|\b(technical|chart) (analysis|setup)\b|\bwall street (wrap|breakfast)\b|"
    r"\bmarket (wrap|movers)\b|shares are (falling|soaring|rising|trading)|"
    r"\b(maintains|reiterates)\b.*\bprice target\b|"
    r"\b(dips?|advances|outperforms|underperforms|registers)\b.*\b(broader )?market\b", re.I)
# Opinion, valuation and recap shapes: dropped unless the headline names a strong event.
_SOFT = re.compile(
    r"\?\s*$|^(should|could|can|is|are|will|does|do|what|why|how)\b|\bvs\.?\b|\bversus\b|"
    r"\b(buy|sell|hold) (now|today|or)\b|\bstock (is|could|looks|may)\b|"
    r"\b(overvalued|undervalued|cheap|expensive|bargain|fair value|intrinsic value|valuation)\b|"
    r"here'?s (why|what|how)|\b(up|down|higher|lower) today\b", re.I)
_STRONG = re.compile(
    r"lawsuit|patent|infring|\bsues?\b|\bsued\b|litigation|probe|investigat|subpoena|settle|"
    r"antitrust|regulator|\b(ftc|doj|sec|cma|fda|epa|faa|nhtsa|nrc|occ)\b|fine[ds]?\b|penalt|"
    r"recall|approv|rejects?|permit|guilty|indict|charged|"
    r"\b(ceo|cfo|coo|chief|chair(man)?|president)\b|resign|appoint|steps? down|ousted|"
    r"acqui|merger|takeover|\bbid\b|divest|spin-?off|buyback|repurchase|dividend|offering|"
    r"convertible|downgrade|upgrade|"
    r"\bcuts?\b.*\bto (hold|sell|neutral|underweight|underperform|market perform)|initiat|"
    r"\bstake\b|activist|contract|agreement|\bdeal\b|partnership|\border(s)?\b|award|"
    r"layoff|job cuts|restructur|strike|outage|breach|cyber|tariff|\bban\b|export|smuggl|"
    r"restat|going concern|material weakness|short[- ]seller|short report|fraud|bankrupt|"
    r"chapter 11|delist|\bhalt|auditor|\bdefault|class action|whistleblow|data leak|"
    r"withdraws? (its )?guidance|suspend|warning|phase (i|ii|iii|1|2|3)|trial", re.I)
# Real only in a factual headline: valuation pieces use these words constantly.
_WEAK = re.compile(r"results|earnings|revenue|\bloss\b|guidance|outlook|forecast|\bbeats?\b|"
                   r"\bmiss(es)?\b|backlog|launch|debut|plant", re.I)
_CAP_ITEM = r"[A-Z][\w.&'’-]*(?:\s+[A-Z][\w.&'’-]*){0,2}"
# Three or more short capitalized list items: a multi-company roundup. Title Case prose with
# commas has longer items and does not match.
_ROUNDUP = re.compile(rf"\b{_CAP_ITEM},\s+{_CAP_ITEM},\s+(?:and\s+)?{_CAP_ITEM}\b")
_STOP = frozenset({
    "The", "A", "An", "And", "Of", "To", "In", "On", "For", "With", "As", "At", "By", "Its", "It",
    "Is", "Are", "After", "Stock", "Shares", "Says", "Inc", "Corp", "Co", "New", "Why", "How",
    "What", "This", "That", "Over", "From", "Into", "Up", "Down", "Report", "Reports", "Q1", "Q2",
    "Q3", "Q4", "US", "U.S.", "Phase", "Trial", "Price", "Target", "Analyst", "Analysts", "Buy",
    "Hold", "Sell", "Rating", "Billion", "Million", "CEO", "CFO", "Here", "Wall", "Street", "AI"})


def config_block(config: Optional[dict]) -> dict:
    """The merged `research.news` block; an absent block is off."""
    block = ((config or {}).get("research") or {}).get("news")
    return {**_DEFAULTS, **(block if isinstance(block, dict) else {})}


def clean(text: Any, limit: int) -> str:
    """Third-party text made safe to store and render: NFKC, whitespace collapsed, Unicode
    control and format characters (bidi overrides, zero-width) removed, capped at `limit`."""
    s = unicodedata.normalize("NFKC", "" if text is None else str(text))
    s = re.sub(r"\s+", " ", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def safe_url(url: Any) -> str:
    """`url` if it is an http(s) URL with no whitespace or control/format characters, else ""."""
    u = str(url or "").strip()
    if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in u):
        return ""
    return u if re.match(r"https?://\S+$", u, re.I) else ""


def reports_label(n: int) -> str:
    return "1 report" if n == 1 else f"{n} reports"


def _core(name: str) -> str:
    core = re.sub(r"^the\s+", "", name.strip(), flags=re.I)
    for _ in range(3):
        core = _SUFFIX.sub("", core).strip()
    return core


def _loose(phrase: str) -> str:
    """Hyphen, space or nothing between words: "Bristol-Myers" matches "Bristol Myers"."""
    return r"[\s-]?".join(re.escape(p) for p in re.split(r"[\s-]+", phrase) if p)


def aliases(ticker: str, name: Optional[str]) -> list[re.Pattern]:
    """Headline patterns that make an article about this company. A ticker of <=3 characters
    counts only in a context form, because bare short tickers collide with words."""
    t = re.escape(ticker)
    if len(ticker) <= 3:
        pats = [re.compile(rf"\({t}\)|[:$]{t}\b|\b{t}\s+(?i:stock|shares)\b|\b{t}['’]s\b")]
    else:
        pats = [re.compile(rf"(?<![A-Za-z]){t}(?![A-Za-z])")]
    core = _core(name or "")
    if core:
        pats.append(re.compile(rf"\b{_loose(core)}\b", re.I))
        first = core.split()[0].strip(",.'’")
        if len(first) >= 4 and first.lower() not in _GENERIC and first.lower() != core.lower():
            pats.append(re.compile(rf"\b{_loose(first)}\b", re.I))
    return pats


def _keys(text: str, alias_words: set[str]) -> set[str]:
    out = set()
    for tok in re.findall(r"\$?\d[\d.,]*[bmk%]?|[A-Z][\w&'’-]*", text):
        t = tok.strip("'’").rstrip(".,")
        if t not in _STOP and t.lower() not in alias_words and len(t) >= 2:
            out.add(t.lower())
    return out


def grade(headline: str) -> int:
    """2 = names a strong event, 1 = a factual headline with a weak event word, 0 = no event."""
    if _STRONG.search(headline):
        return 2
    return 1 if (not _SOFT.search(headline) and _WEAK.search(headline)) else 0


def select(articles: list, ticker: str, name: Optional[str], max_items: int) -> list[NewsItem]:
    """At most `max_items` event clusters about this company, newest first. Pure."""
    pats = aliases(ticker, name)
    alias_words = {w.lower() for w in re.findall(r"[A-Za-z][\w&'-]*", name or "")} | {ticker.lower()}
    clusters: list[dict] = []
    rows = [a for a in articles if isinstance(a, dict) and isinstance(a.get("datetime"), (int, float))]
    for a in sorted(rows, key=lambda a: a["datetime"]):
        h = clean(a.get("headline"), 1000)
        if not h or not any(p.search(h) for p in pats):
            continue
        g = grade(h)
        if (a.get("source") in _OPINION_SOURCES or _DROP.search(h) or _ROUNDUP.search(h)
                or (_SOFT.search(h) and g == 0)):
            continue
        summary = clean(a.get("summary"), 1000)
        keys = _keys(h + " " + summary[:160], alias_words)
        member = {"ts": a["datetime"], "grade": g, "headline": h, "summary": summary,
                  "url": a.get("url"), "source": clean(a.get("source"), 40)}
        for c in clusters:
            if (abs(member["ts"] - c["last_ts"]) <= _CLUSTER_SECONDS
                    and len(keys & c["seed"]) >= _MIN_SHARED_KEYS):
                c["members"].append(member)
                c["last_ts"] = max(c["last_ts"], member["ts"])
                break
        else:
            clusters.append({"seed": keys, "first_ts": member["ts"], "last_ts": member["ts"],
                             "members": [member]})
    eligible = []
    for c in clusters:
        # The face is the strongest member, so a fluff headline never fronts a real event.
        face = max(c["members"], key=lambda m: (m["grade"], -m["ts"]))
        if face["grade"]:
            eligible.append((c, face))
    eligible.sort(key=lambda cf: (min(len(cf[0]["members"]), _REPORTS_CAP), cf[0]["last_ts"]),
                  reverse=True)
    shown = sorted(eligible[:max(0, max_items)], key=lambda cf: cf[0]["first_ts"], reverse=True)
    return [NewsItem(
        date=datetime.fromtimestamp(c["first_ts"], tz=timezone.utc).date().isoformat(),
        source=clean(html.unescape(face["source"]), 40),
        headline=clean(html.unescape(face["headline"]), _HEADLINE_MAX),
        summary=clean(html.unescape(face["summary"]), _SUMMARY_MAX), url=safe_url(face["url"]),
        reports=len(c["members"])) for c, face in shown]


def _get(key: str, params: dict, deadline_s: float, transport: Any) -> Any:
    """The parsed body within `deadline_s` of wall clock. An httpx timeout is per read, not per
    request, and name resolution is outside it, so the request runs in a daemon thread and the
    caller waits `deadline_s`, no longer. An abandoned thread holds one socket until the
    server or a read timeout ends it."""
    import httpx

    box: dict = {}

    def work() -> None:
        try:
            with httpx.Client(timeout=deadline_s, transport=transport,
                              headers={"X-Finnhub-Token": key}) as client:
                r = client.get(URL, params=params)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            box["body"] = r.json()
        except Exception as e:          # noqa: BLE001 — carried to the caller below
            box["error"] = e

    worker = threading.Thread(target=work, name="news-fetch", daemon=True)
    worker.start()
    worker.join(deadline_s)
    if worker.is_alive():
        raise TimeoutError("the request is over the deadline")
    if "error" in box:
        raise box["error"]
    return box["body"]


def fetch_articles(ticker: str, cfg: dict, *, today: Optional[date] = None,
                   transport: Any = None) -> Optional[list]:
    """The raw `company-news` list for the lookback window, or None. Never raises. The key
    travels in a header, never the URL, so no error string can carry it. No retry: a 429 is
    simply unavailable."""
    from .filings import log_abstain

    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        log_abstain("news fetch skipped", ticker, RuntimeError("FINNHUB_API_KEY is not set"))
        return None
    today = today or datetime.now(timezone.utc).date()
    params = {"symbol": ticker.upper(), "to": today.isoformat(),
              "from": (today - timedelta(days=int(cfg["lookback_days"]))).isoformat()}
    try:
        deadline_s = min(max(float(cfg["timeout"]), 0.1), MAX_DEADLINE_S)
        body = _get(key, params, deadline_s, transport)
        if not isinstance(body, list):
            raise ValueError("company-news body is not a list")
        return body
    except Exception as e:      # noqa: BLE001 — never-raises contract
        log_abstain("news fetch failed", ticker, RuntimeError(str(e).replace(key, "***")))
        return None


@dataclass
class NewsSection:
    """What the brief records: the items, a status, the fetch time, the first date the items
    can come from, and whether the feed's article cap cut the window short."""
    items: list[NewsItem] = field(default_factory=list)
    status: str = UNAVAILABLE
    as_of: str = ""
    covers_from: str = ""
    truncated: bool = False


def _covered(articles: list, window_start: date) -> tuple[str, bool]:
    stamps = [a["datetime"] for a in articles
              if isinstance(a, dict) and isinstance(a.get("datetime"), (int, float))]
    if len(articles) < TRUNCATE_AT or not stamps:
        return window_start.isoformat(), False
    oldest = datetime.fromtimestamp(min(stamps), tz=timezone.utc).date()
    return max(oldest, window_start).isoformat(), True


def fetch_section(card: Any, config: Optional[dict], *, now: Optional[datetime] = None,
                  transport: Any = None) -> NewsSection:
    """The section for the brief. Never raises. Any failure is UNAVAILABLE, which the brief
    prints, so an outage cannot read as "no news"."""
    now = now or datetime.now(timezone.utc)
    as_of = now.strftime("%Y-%m-%d %H:%M UTC")
    ticker = str(getattr(card, "ticker", "") or "")
    try:
        cfg = config_block(config)
        window_start = now.date() - timedelta(days=int(cfg["lookback_days"]))
        name = getattr(getattr(card, "metrics", None), "name", None)
        articles = fetch_articles(ticker, cfg, today=now.date(), transport=transport)
        if articles is None:
            return NewsSection(as_of=as_of, covers_from=window_start.isoformat())
        items = select(articles, ticker, name if isinstance(name, str) else None,
                       int(cfg["max_items"]))
        covers_from, truncated = _covered(articles, window_start)
        return NewsSection(items, SHOWN if items else NONE, as_of, covers_from, truncated)
    except Exception as e:      # noqa: BLE001 — never-raises contract
        from .filings import log_abstain
        log_abstain("news section failed", ticker or "?", e)
        return NewsSection(as_of=as_of)


_MD_SPECIAL = re.compile(r"([\\`*_\[\]()<>#|~])")


def _md(text: str) -> str:
    """Escaped for inline Markdown, including a leading list marker ("1. ", "- ", "+ ")."""
    s = _MD_SPECIAL.sub(r"\\\1", text)
    s = re.sub(r"^(\d+)\.(?=\s)", r"\1\\.", s)
    return re.sub(r"^([-+])(?=\s)", r"\\\1", s)


def note(as_of: str, covers_from: str, truncated: bool) -> str:
    text = NOTE.format(covers_from=covers_from or "?", as_of=as_of)
    return f"{text} {TRUNCATED_TEXT}" if truncated else text


def markdown_lines(items: list, status: str, as_of: str, covers_from: str = "",
                   truncated: bool = False) -> list[str]:
    """The brief's section, starting with a blank line; [] when the section is off."""
    if not status:
        return []
    lines = ["", f"## {HEADING}", f"_{note(as_of, covers_from, truncated)}_"]
    if status == UNAVAILABLE:
        return lines + [f"_{UNAVAILABLE_TEXT}_"]
    if not items:
        return lines + [f"_{NONE_TEXT}_"]
    for it in items:
        head = _md(it.headline)
        url = safe_url(it.url).replace("(", "%28").replace(")", "%29")
        lines.append(f"- {it.date} · {_md(it.source)} · {reports_label(it.reports)} — "
                     + (f"[{head}]({url})" if url else head))
        if it.summary:
            lines.append(f"  {_md(it.summary)}")
    return lines
