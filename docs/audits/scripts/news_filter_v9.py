"""Filter v9 (shipped in research/news.py): v8 plus "cut N jobs" / "lay off" as strong events,
past-quarter peer earnings roundups dropped, and a company named only as a former employer not
counted as on-company. Evidence: docs/audits/2026-10-18-deep-news-usefulness-review.md.
"""
import html
import math
import re
import unicodedata
from collections import Counter
from datetime import datetime, timezone

A, B, C, D = True, False, False, True

_SUFFIX = re.compile(r"[,.]?\s+(inc|incorporated|corp|corporation|co|company|holdings?|group|ltd|plc|n\.?v|s\.?a|class [a-c])\.?$", re.I)
_GENERIC = {"the", "first", "general", "american", "united", "national", "international", "global", "new", "bank"}
_OPINION_SOURCES = {"SeekingAlpha", "ChartMill"}

_FORMER = re.compile(r"\b(former\s+|ex-)(\S+\s+){0,2}$", re.I)
_DROP = re.compile(
    r"^prediction:|\bearnings roundup\b|\breflecting on\b.{0,60}\bearnings\b|\btop \d+\b|\b\d+ (stocks|reasons|things)\b|\bstocks to (watch|buy)\b|"
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
    r"layoff|job cuts|\blay(s|ing)? off\b|\bcut(s|ting)? .{0,20}\bjobs\b|restructur|strike|outage|breach|cyber|tariff|\bban\b|export|smuggl|"
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
_CLUSTER_SECONDS = 4 * 86400
_MERGE_COS = 0.20
_DEAL_SECONDS = 7 * 86400
# The merge pass (see `_merge`). A company has a results or a layoff event at most once per
# window, so two such clusters are one story without a shared key. Trial data and dividends
# are excluded from "results": they ride along with earnings but are events of their own.
_ONCE = (re.compile(r"^(?!.*\b(trial|study|phase|data|dividend)\b)"
                    r".*(\bq[1-4]\b|quarter|results|earnings|\beps\b)", re.I),
         re.compile(r"layoffs?|\blay(s|ing)? off\b|job cuts|\bcuts? .{0,20}\bjobs\b|"
                    r"\b(cut|cuts|cutting|reduc\w*|trim\w*|shrink\w*) .{0,20}workforce", re.I))
# "to buy" only after a deal verb: "upgrades Acme to Buy" is a rating, not a deal.
_DEAL = re.compile(r"acqui|merger|takeover|buyout|\bbids?\b|tie-?up|megadeal|\bbuys\b|"
                   r"\b(plans?|agrees?|agreed|offers?|offered|seeks?|wants?|eyes|eyeing|"
                   r"considers?|considering|explores?|explored|exploring|weighs?|weighing|"
                   r"talks) to buy\b", re.I)
# Words that are capitalized in a headline but never name a deal's counterparty.
_GENERIC_KEYS = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january",
    "february", "march", "april", "june", "july", "august", "september", "october", "november",
    "december", "nasdaq", "nyse", "reuters", "bloomberg", "takeover", "acquisition", "merger",
    "deal", "bid", "rumors", "even", "buyout", "offer"})
# A development that changes the story stays its own item. Past-tense "completed"/"closed"
# would match "deal being completed".
_REVERSAL = re.compile(r"\b(den(y|ies|ied)|rules? out|ruled out|walks? away|withdr[ae]w\w*|"
                       r"abandon\w*|terminat\w*|calls? off|scrap\w*|rejects?|rejected|"
                       r"completes|closes|blocks?|blocked)\b", re.I)
_TF_STOP = frozenset({
    "about", "above", "after", "again", "against", "all", "also", "and", "any", "are", "because",
    "been", "before", "being", "below", "between", "both", "but", "can", "could", "did", "does",
    "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "her", "here", "hers", "him", "his", "how", "into", "its", "itself", "just", "more",
    "most", "nor", "not", "now", "off", "once", "only", "other", "our", "out", "over", "own",
    "same", "she", "should", "some", "such", "than", "that", "the", "their", "them", "then",
    "there", "these", "they", "this", "those", "through", "too", "under", "until", "very", "was",
    "were", "what", "when", "where", "which", "while", "who", "whom", "why", "will", "with",
    "would", "you", "your", "says", "said", "new", "stock", "stocks", "shares", "share", "inc",
    "corp", "company", "report", "reports", "reported", "according", "week", "today", "year",
    "years", "first", "one", "two", "three", "per", "million", "billion", "percent", "amid",
    "may", "likely"})
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


def _clean(text, limit):
    """research/news.py:clean, so the merge reads the same summary text as production."""
    s = unicodedata.normalize("NFKC", "" if text is None else str(text))
    s = re.sub(r"\s+", " ", s)
    s = "".join(ch for ch in s if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _vectors(members: list[dict], alias_words: set[str]) -> list[dict[str, float]]:
    """L2-normed TF-IDF of headline + summary start; idf counts this company's own articles."""
    docs = [Counter(w for w in re.findall(r"[a-z0-9$%][a-z0-9$%.'-]*[a-z0-9%]",
                                          (m["headline"] + " " + m["summary"][:300]).lower())
                    if w not in _TF_STOP and w not in alias_words and len(w) >= 3)
            for m in members]
    df = Counter(w for d in docs for w in d)
    out = []
    for d in docs:
        v = {w: (1 + math.log(c)) * math.log((1 + len(docs)) / (1 + df[w])) for w, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _merge(clusters: list[dict]) -> list[list[dict]]:
    """The seed clusters regrouped so one event is one group; every member is kept exactly once.
    A merge compares the clusters' original wording and keys and never targets a cluster that
    joined another, so a story cannot snowball. Rules: docs/RESEARCH.md → the recent-news section."""
    info = []
    for c in clusters:
        acc: Counter = Counter()
        for m in c["members"]:
            acc.update(m["vec"])
        norm = math.sqrt(sum(x * x for x in acc.values())) or 1.0
        h = max(c["members"], key=lambda m: (m["grade"], -m["ts"]))
        info.append({"cent": {w: x / norm for w, x in acc.items()}, "first": c["first_ts"],
                     "once": {i for i, p in enumerate(_ONCE) if p.search(h["headline"])},
                     "deal": bool(_DEAL.search(h["headline"])),
                     "keys": (h["keys"] | c["members"][0]["keys"]) - _GENERIC_KEYS,
                     "rev": bool(_REVERSAL.search(h["headline"]))})
    order = sorted(range(len(clusters)), key=lambda i: info[i]["first"])
    target: dict[int, int] = {}
    for pos, j in enumerate(order):
        b = info[j]
        for i in order[:pos]:
            a = info[i]
            if i in target or a["rev"] != b["rev"]:
                continue
            dt = abs(b["first"] - a["first"])
            if ((dt <= _CLUSTER_SECONDS and (_cos(a["cent"], b["cent"]) >= _MERGE_COS
                                             or a["once"] & b["once"]))
                    or (a["deal"] and b["deal"] and a["keys"] & b["keys"] and dt <= _DEAL_SECONDS)):
                target[j] = i
                break
    groups: dict[int, list[dict]] = {}
    for j, c in enumerate(clusters):
        groups.setdefault(target.get(j, j), []).extend(c["members"])
    return list(groups.values())


def select(articles, ticker, name, k=12, window_days=4, min_shared=2):
    strong = _strong()
    assert _RATING_IN_STRONG in _STRONG.pattern and _TITLE_IN_STRONG in _STRONG.pattern
    pats = aliases(ticker, name)
    alias_words = {w.lower() for w in re.findall(r"[A-Za-z][\w&'-]*", name or "")} | {ticker.lower()}
    stats = {"total": len(articles), "on_company": 0, "dropped": 0, "clustered": 0}
    clusters, members = [], []
    for a in sorted(articles, key=lambda a: (a.get("datetime") or 0, str(a.get("id")))):
        h = html.unescape(a.get("headline") or "").strip()
        hits = [m for p in pats for m in p.finditer(h)] if h else []
        if not hits or all(_FORMER.search(h[:m.start()]) for m in hits):
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
        member = {"id": a.get("id"), "ts": ts, "grade": g, "headline": _clean(h, 1000),
                  "summary": _clean(html.unescape(a.get("summary") or ""), 1000),
                  "url": a.get("url"), "source": a.get("source"), "keys": keys}
        members.append(member)
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
    for m, v in zip(members, _vectors(members, alias_words), strict=True):
        m["vec"] = v
    eligible = []
    for group in _merge(clusters):
        face = max(group, key=lambda m: (m["grade"], -m["ts"]))
        if face["grade"] == 0:
            continue
        eligible.append(({"members": group, "first_ts": min(m["ts"] for m in group),
                          "last_ts": max(m["ts"] for m in group)}, face))
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
