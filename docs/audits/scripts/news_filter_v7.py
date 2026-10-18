"""Filter v7 (shipped in research/news.py): v6 plus precision changes from the §8 usefulness
review (docs/audits/2026-10-18-deep-news-usefulness-review.md), each switchable (A..D).

A  drop non-event shapes: previews, call notices, transcripts, Zacks-rank upgrades,
   price-target roundups, dividend and "still a buy" columns, options-move pieces; "dividend"
   is an event word only for a dividend action. Broker rating changes stay events: the 24-name
   labels count them as high-tier, and dropping them took high-tier recall from 43 to 39 of 47.
B  an executive title alone is not an event. Not adopted: recall 42 of 47.
C  at most one weak earnings cluster per 5 days. Not adopted: little gain, lower precision.
D  rank strong events above weak ones before syndication count. Adopted.

Measured 2026-10-18 with probe_news_eval_k10.py (24 labelled names, K=10): v6 precision 135/181,
high-tier recall 43/47; v7 (A+D) 130/176 and 44/47. On a fresh pull of the 8 review names the
shown items the panel had rated went from 18 wanted / 34 not to 17 / 17. Those items informed
A, so the acceptance test is the same panel on 8 new tickers.
"""
import html
import re
from datetime import datetime, timezone

A, B, C, D = True, False, False, True

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
    r"acqui|merger|takeover|\bbid\b|divest|spin-?off|buyback|repurchase|offering|convertible|"
    r"\b(raises?|hikes?|cuts?|suspends?|declares?|boosts?|increases?|slashes?|eliminates?|"
    r"reinstates?)\b.{0,30}\bdividend|\bdividend (raise|hike|cut|increase|suspension)|"
    r"downgrade|upgrade|\bcuts?\b.*\bto (hold|sell|neutral|underweight|underperform|market perform)|initiat|"
    r"\bstake\b|activist|contract|agreement|\bdeal\b|partnership|\border(s)?\b|award|"
    r"layoff|job cuts|restructur|strike|outage|breach|cyber|tariff|\bban\b|export|smuggl|"
    r"restat|going concern|material weakness|short[- ]seller|short report|fraud|bankrupt|chapter 11|"
    r"delist|\bhalt|auditor|\bdefault|class action|whistleblow|data leak|withdraws? (its )?guidance|"
    r"suspend|warning|"
    r"phase (i|ii|iii|1|2|3)|trial", re.I)
_DROP_A = re.compile(
    r"what to expect|\bahead of (its |the )?(q[1-4] |quarterly )?(earnings|results|report)\b|"
    r"\bheads? into earnings\b|\b(announces|to host|will host|schedules)\b.{0,40}\b(conference|earnings) call\b|"
    r"\b(will|to) (announce|report|release)\b.{0,60}\bresults\b|\breports? earnings (tomorrow|today|this week|next week)\b|"
    r"\btranscript\b|\bzacks rank\b|\bupgraded to (strong )?buy: here|"
    r"\bprice target (hikes?|raises?|boosts?|bumps?)\b|\b(slew|series|round) of price target|"
    r"\bdividend (king|royalty|aristocrat)|\bcramer\b|\bstill a buy\b|\bwhat investors need to know\b|"
    r"\bwhat you should know\b|\bcould go by 20\d\d\b|"
    r"\btraders see\b.{0,40}\bmov|\bimplied move\b", re.I)
_RATING_IN_STRONG = r"downgrade|upgrade|\bcuts?\b.*\bto (hold|sell|neutral|underweight|underperform|market perform)|initiat|"
_TITLE_IN_STRONG = r"\b(ceo|cfo|coo|chief|chair(man)?|president)\b|"
_MGMT = (r"\b(names?|named|hires?|hired|promotes?|promoted|succeeds?|successor|retires?|retirement|departs?|departure)\b"
         r".{0,60}\b(ceo|cfo|coo|chief|chair(man|woman)?|president|officer|director)\b|"
         r"\b(ceo|cfo|coo|chief|chair(man|woman)?|president|officer|director)\b.{0,60}"
         r"\b(named|resigns?|retires?|departs?|to step down|exits?)\b|")
_EARN = re.compile(r"\b(q[1-4]|(first|second|third|fourth)[- ]quarter|quarterly|results|earnings|guidance|outlook|eps)\b", re.I)


def _strong():
    pat = _STRONG.pattern
    if B:
        pat = pat.replace(_TITLE_IN_STRONG, _MGMT)
    return re.compile(pat, re.I)


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


def _grade(h, strong):
    """2 = strong event headline, 1 = factual headline with a weak event word, 0 = no event."""
    soft = bool(_SOFT.search(h))
    if strong.search(h):
        return 2
    return 1 if (not soft and _WEAK.search(h)) else 0


def select(articles, ticker, name, k=12, window_days=4, min_shared=2):
    strong = _strong()
    assert _RATING_IN_STRONG in _STRONG.pattern and _TITLE_IN_STRONG in _STRONG.pattern
    pats = aliases(ticker, name)
    alias_words = {w.lower() for w in re.findall(r"[A-Za-z][\w&'-]*", name or "")} | {ticker.lower()}
    stats = {"total": len(articles), "on_company": 0, "dropped": 0, "clustered": 0}
    clusters = []
    for a in sorted(articles, key=lambda a: a.get("datetime") or 0):
        h = html.unescape(a.get("headline") or "").strip()
        if not h or not any(p.search(h) for p in pats):
            continue
        stats["on_company"] += 1
        g = _grade(h, strong)
        if (a.get("source") in _OPINION_SOURCES or _DROP.search(h) or _ROUNDUP.search(h)
                or (A and _DROP_A.search(h))
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
    eligible.sort(key=lambda cf: ((cf[1]["grade"] if D else 0), min(len(cf[0]["members"]), 5),
                                  cf[0]["last_ts"]), reverse=True)
    if C:
        kept, earn = [], []
        for c, f in sorted(eligible, key=lambda cf: len(cf[0]["members"]), reverse=True):
            if f["grade"] == 1 and _EARN.search(f["headline"]):
                if any(abs(c["first_ts"] - e) <= 5 * 86400 for e in earn):
                    continue
                earn.append(c["first_ts"])
            kept.append((c, f))
        eligible = [cf for cf in eligible if cf in kept]
    out = sorted(eligible[:k], key=lambda cf: cf[0]["last_ts"], reverse=True)
    return [{"date": datetime.fromtimestamp(c["first_ts"], tz=timezone.utc).date().isoformat(),
             "source": f["source"], "headline": f["headline"], "summary": f["summary"],
             "url": f["url"], "outlets": len(c["members"]),
             "ids": [m["id"] for m in c["members"]]} for c, f in out], stats
