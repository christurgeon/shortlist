"""Prototype filter v4 (throwaway): Finnhub company-news -> <=K on-company event clusters."""
import re
from datetime import datetime, timezone

_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|holdings?|group|ltd|plc|n\.?v|s\.?a|class [a-c])\.?$", re.I)
_GENERIC = {"the", "first", "general", "american", "united", "national", "international", "global", "new", "bank"}
_OPINION_SOURCES = {"SeekingAlpha", "ChartMill"}

# Hard drops: never an event, whatever else the headline says.
_DROP = re.compile(
    r"^prediction:|\btop \d+\b|\b\d+ (stocks|reasons|things)\b|\bstocks to (watch|buy)\b|"
    r"facts to (know|note)|trending stock|what you need to know|important facts|analyst blog|"
    r"some (facts|information) |things to know|\bearnings preview\b|\bunusual options\b|"
    r"\boptions activity\b|\b(technical|chart) (analysis|setup)\b|\bwall street (wrap|breakfast)\b|"
    r"\bmarket (wrap|movers)\b|shares are (falling|soaring|rising|trading)|"
    r"\b(maintains|reiterates)\b.*\bprice target\b|"
    r"\b(dips?|advances|outperforms|underperforms|registers)\b.*\b(broader )?market\b", re.I)
# Soft: opinion/valuation/recap shape. Dropped UNLESS the headline itself names an event.
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
    r"phase (i|ii|iii|1|2|3)|trial", re.I)
# Weak: real only in a factual headline; valuation pieces lean on these words constantly.
_WEAK = re.compile(
    r"results|earnings|revenue|\bloss\b|guidance|outlook|forecast|\bbeats?\b|\bmiss(es)?\b|"
    r"backlog|buyback|repurchase|dividend|launch|debut|plant", re.I)
_ROUNDUP = re.compile(r"(\b[A-Z][\w.&'’-]+(?: [A-Z][\w.&'’-]+)*,\s*){2,}")
_STOP = {"The", "A", "An", "And", "Of", "To", "In", "On", "For", "With", "As", "At", "By", "Its", "It",
         "Is", "Are", "After", "Stock", "Shares", "Says", "Inc", "Corp", "Co", "New", "Why", "How",
         "What", "This", "That", "Over", "From", "Into", "Up", "Down", "Report", "Reports", "Q1", "Q2",
         "Q3", "Q4", "US", "U.S.", "Phase", "Trial", "Price", "Target", "Analyst", "Analysts", "Buy",
         "Hold", "Sell", "Rating", "Billion", "Million", "CEO", "CFO", "Here", "Wall", "Street", "AI"}


def aliases(ticker, name):
    core = name
    for _ in range(3):
        core = _SUFFIX.sub("", core).strip()
    pats = [re.compile(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])"),
            re.compile(rf"\b{re.escape(core)}\b", re.I)]
    first = core.split()[0].strip(",.'’") if core.split() else ""
    if len(first) >= 4 and first.lower() not in _GENERIC and first.lower() != core.lower():
        pats.append(re.compile(rf"\b{re.escape(first)}\b", re.I))
    return pats


def _keys(text, alias_words):
    toks = re.findall(r"\$?\d[\d.,]*[bmk%]?|[A-Z][\w&'’-]*", text)
    out = set()
    for t in toks:
        t2 = t.strip("'’").rstrip(".,")
        if t2 in _STOP or t2.lower() in alias_words or len(t2) < 2:
            continue
        out.add(t2.lower())
    return out


def select(articles, ticker, name, k=12, window_days=4, min_shared=2):
    pats = aliases(ticker, name)
    alias_words = {w.lower() for w in re.findall(r"[A-Za-z][\w&'-]*", name)} | {ticker.lower()}
    stats = {"total": len(articles), "on_company": 0, "dropped": 0, "clustered": 0}
    clusters = []
    for a in sorted(articles, key=lambda a: a.get("datetime") or 0):   # oldest first: first report leads
        h = (a.get("headline") or "").strip()
        if not h or not any(p.search(h) for p in pats):
            continue
        stats["on_company"] += 1
        soft = bool(_SOFT.search(h))
        ev = bool(_STRONG.search(h)) or (not soft and bool(_WEAK.search(h)))
        if (a.get("source") in _OPINION_SOURCES or _DROP.search(h) or _ROUNDUP.search(h)
                or (soft and not ev)):
            stats["dropped"] += 1
            continue
        keys = _keys(h + " " + (a.get("summary") or "")[:160], alias_words)
        ts = a.get("datetime") or 0
        home = None
        for c in clusters:
            if abs(ts - c["last_ts"]) <= window_days * 86400 and len(keys & c["seed"]) >= min_shared:
                home = c; break
        if home:
            home["ids"].append(a.get("id")); home["outlets"] += 1
            home["last_ts"] = max(home["last_ts"], ts); home["event"] |= ev
            if home["soft"] and not soft:       # prefer a factual headline as the face
                home.update(headline=h, summary=(a.get("summary") or "").strip(), url=a.get("url"),
                            source=a.get("source"), soft=False)
            stats["clustered"] += 1
            continue
        clusters.append({"ids": [a.get("id")], "outlets": 1, "seed": keys, "first_ts": ts, "last_ts": ts,
                         "event": ev, "soft": soft, "headline": h,
                         "summary": (a.get("summary") or "").strip(), "url": a.get("url"),
                         "source": a.get("source")})
    stats["clusters"] = len(clusters)
    clusters = [c for c in clusters if c["event"]]          # never pad a quiet name with non-events
    clusters.sort(key=lambda c: (min(c["outlets"], 5), c["last_ts"]), reverse=True)
    out = sorted(clusters[:k], key=lambda c: c["last_ts"], reverse=True)
    return [{"date": datetime.fromtimestamp(c["first_ts"], tz=timezone.utc).date().isoformat(),
             "source": c["source"], "headline": c["headline"], "summary": c["summary"],
             "url": c["url"], "outlets": c["outlets"], "ids": c["ids"]} for c in out], stats
