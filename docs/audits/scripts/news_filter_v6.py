"""Filter v6 (shipped in research/news.py): v5 with headlines and summaries HTML-unescaped
before matching, because Finnhub sends entities ("AT&amp;T"). Every other rule is v5's.
Evidence: docs/audits/2026-10-16-deep-news-leads-design.md §4.2.
"""
import html
import re
from datetime import datetime, timezone

_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|holdings?|group|ltd|plc|n\.?v|s\.?a|class [a-c])\.?$", re.I)
_GENERIC = {"the", "first", "general", "american", "united", "national", "international", "global", "new", "bank"}
_OPINION_SOURCES = {"SeekingAlpha", "ChartMill"}

_DROP = re.compile(
    r"^prediction:|\btop \d+\b|\b\d+ (stocks|reasons|things)\b|\bstocks to (watch|buy)\b|"
    r"facts to (know|note)|trending stock|what you need to know|important facts|analyst blog|"
    r"some (facts|information) |things to know|\bearnings preview\b|\bunusual options\b|"
    r"\boptions activity\b|\b(technical|chart) (analysis|setup)\b|\bwall street (wrap|breakfast)\b|"
    r"\bmarket (wrap|movers)\b|shares are (falling|soaring|rising|trading)|"
    r"\b(maintains|reiterates)\b.*\bprice target\b|"
    r"\b(dips?|advances|outperforms|underperforms|registers)\b.*\b(broader )?market\b", re.I)
_SOFT = re.compile(
    r"\?\s*$|^(should|could|can|is|are|will|does|do|what|why|how)\b|\bvs\.?\b|\bversus\b|"
    r"\b(buy|sell|hold) (now|today|or)\b|\bstock (is|could|looks|may)\b|"
    r"\b(overvalued|undervalued|cheap|expensive|bargain|fair value|intrinsic value|valuation)\b|"
    r"here'?s (why|what|how)|\b(up|down|higher|lower) today\b", re.I)
_STRONG = re.compile(
    r"lawsuit|patent|infring|\bsues?\b|\bsued\b|litigation|probe|investigat|subpoena|settle|antitrust|regulator|"
    r"\b(ftc|doj|sec|cma|fda|epa|faa|nhtsa|nrc|occ)\b|fine[ds]?\b|penalt|recall|approv|rejects?|permit|"
    r"guilty|indict|charged|\b(ceo|cfo|coo|chief|chair(man)?|president)\b|resign|appoint|steps? down|ousted|"
    r"acqui|merger|takeover|\bbid\b|divest|spin-?off|buyback|repurchase|dividend|offering|convertible|"
    r"downgrade|upgrade|\bcuts?\b.*\bto (hold|sell|neutral|underweight|underperform|market perform)|initiat|"
    r"\bstake\b|activist|contract|agreement|\bdeal\b|partnership|\border(s)?\b|award|"
    r"layoff|job cuts|restructur|strike|outage|breach|cyber|tariff|\bban\b|export|smuggl|"
    r"restat|going concern|material weakness|short[- ]seller|short report|fraud|bankrupt|chapter 11|"
    r"delist|\bhalt|auditor|\bdefault|class action|whistleblow|data leak|withdraws? (its )?guidance|"
    r"suspend|warning|"
    r"phase (i|ii|iii|1|2|3)|trial", re.I)
_WEAK = re.compile(
    r"results|earnings|revenue|\bloss\b|guidance|outlook|forecast|\bbeats?\b|\bmiss(es)?\b|"
    r"backlog|launch|debut|plant", re.I)
_CAP_ITEM = r"[A-Z][\w.&'’-]*(?:\s+[A-Z][\w.&'’-]*){0,2}"
_ROUNDUP = re.compile(rf"\b{_CAP_ITEM},\s+{_CAP_ITEM},\s+(?:and\s+)?{_CAP_ITEM}\b")
_STOP = {"The", "A", "An", "And", "Of", "To", "In", "On", "For", "With", "As", "At", "By", "Its", "It",
         "Is", "Are", "After", "Stock", "Shares", "Says", "Inc", "Corp", "Co", "New", "Why", "How",
         "What", "This", "That", "Over", "From", "Into", "Up", "Down", "Report", "Reports", "Q1", "Q2",
         "Q3", "Q4", "US", "U.S.", "Phase", "Trial", "Price", "Target", "Analyst", "Analysts", "Buy",
         "Hold", "Sell", "Rating", "Billion", "Million", "CEO", "CFO", "Here", "Wall", "Street", "AI"}


def _core(name):
    core = re.sub(r"^the\s+", "", (name or "").strip(), flags=re.I)
    for _ in range(3):
        core = _SUFFIX.sub("", core).strip()
    return core


def _loose(phrase):
    parts = [re.escape(p) for p in re.split(r"[\s-]+", phrase) if p]
    return r"[\s-]?".join(parts)


def aliases(ticker, name):
    t = re.escape(ticker)
    if len(ticker) <= 3:
        pats = [re.compile(rf"\({t}\)|[:$]{t}\b|\b{t}\s+(?i:stock|shares)\b|\b{t}['’]s\b")]
    else:
        pats = [re.compile(rf"(?<![A-Za-z]){t}(?![A-Za-z])")]
    core = _core(name)
    if core:
        pats.append(re.compile(rf"\b{_loose(core)}\b", re.I))
        first = core.split()[0].strip(",.'’")
        if len(first) >= 4 and first.lower() not in _GENERIC and first.lower() != core.lower():
            pats.append(re.compile(rf"\b{_loose(first)}\b", re.I))
    return pats


def _keys(text, alias_words):
    out = set()
    for tok in re.findall(r"\$?\d[\d.,]*[bmk%]?|[A-Z][\w&'’-]*", text):
        t2 = tok.strip("'’").rstrip(".,")
        if t2 in _STOP or t2.lower() in alias_words or len(t2) < 2:
            continue
        out.add(t2.lower())
    return out


def _grade(h):
    """2 = strong event headline, 1 = factual headline with a weak event word, 0 = no event."""
    soft = bool(_SOFT.search(h))
    if _STRONG.search(h):
        return 2
    return 1 if (not soft and _WEAK.search(h)) else 0


def select(articles, ticker, name, k=12, window_days=4, min_shared=2):
    pats = aliases(ticker, name)
    alias_words = {w.lower() for w in re.findall(r"[A-Za-z][\w&'-]*", name or "")} | {ticker.lower()}
    stats = {"total": len(articles), "on_company": 0, "dropped": 0, "clustered": 0}
    clusters = []
    for a in sorted(articles, key=lambda a: a.get("datetime") or 0):
        h = html.unescape(a.get("headline") or "").strip()
        if not h or not any(p.search(h) for p in pats):
            continue
        stats["on_company"] += 1
        g = _grade(h)
        if (a.get("source") in _OPINION_SOURCES or _DROP.search(h) or _ROUNDUP.search(h)
                or (_SOFT.search(h) and g == 0)):
            stats["dropped"] += 1
            continue
        keys = _keys(h + " " + html.unescape(a.get("summary") or "")[:160], alias_words)
        ts = a.get("datetime") or 0
        member = {"id": a.get("id"), "ts": ts, "grade": g, "headline": h,
                  "summary": html.unescape(a.get("summary") or "").strip(), "url": a.get("url"),
                  "source": a.get("source")}
        home = None
        for c in clusters:
            if abs(ts - c["last_ts"]) <= window_days * 86400 and len(keys & c["seed"]) >= min_shared:
                home = c
                break
        if home:
            home["members"].append(member)
            home["last_ts"] = max(home["last_ts"], ts)
            stats["clustered"] += 1
        else:
            clusters.append({"seed": keys, "first_ts": ts, "last_ts": ts, "members": [member]})
    stats["clusters"] = len(clusters)
    eligible = []
    for c in clusters:
        face = max(c["members"], key=lambda m: (m["grade"], -m["ts"]))
        if face["grade"] == 0:
            continue
        eligible.append((c, face))
    eligible.sort(key=lambda cf: (min(len(cf[0]["members"]), 5), cf[0]["last_ts"]), reverse=True)
    out = sorted(eligible[:k], key=lambda cf: cf[0]["last_ts"], reverse=True)
    return [{"date": datetime.fromtimestamp(c["first_ts"], tz=timezone.utc).date().isoformat(),
             "source": f["source"], "headline": f["headline"], "summary": f["summary"],
             "url": f["url"], "outlets": len(c["members"]),
             "ids": [m["id"] for m in c["members"]]} for c, f in out], stats
