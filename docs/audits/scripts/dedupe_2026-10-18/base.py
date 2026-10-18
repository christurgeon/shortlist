"""Shared pieces for the dedupe prototypes: v7's article filter (unchanged), v7's seed
clustering, and v7's face/rank/cut. A prototype replaces only the clustering step.

Every prototype module exposes
    clusters(articles, ticker, name) -> list[list[member]]   (all clusters, before ranking)
    select(articles, ticker, name, k) -> (items, stats)       (same shape as news_filter_v7)
"""
import html
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "v7", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "news_filter_v7.py"))
v7 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v7)

DAY = 86400
SPLIT_HYPHEN = False      # experiment: "Starbucks-Chipotle" -> "starbucks", "chipotle"
FACE_BY_SUB = False       # experiment: a merged cluster is fronted by its largest constituent
CLUSTER_SECONDS = 4 * DAY


def alias_words(ticker, name):
    return {w.lower() for w in re.findall(r"[A-Za-z][\w&'-]*", name or "")} | {ticker.lower()}


def prefilter(articles, ticker, name):
    """v7's on-company + drop rules; returns members sorted by time (same order as v7)."""
    strong = v7._strong()
    pats = v7.aliases(ticker, name)
    aw = alias_words(ticker, name)
    out = []
    for a in sorted(articles, key=lambda a: a.get("datetime") or 0):
        h = html.unescape(a.get("headline") or "").strip()
        if not h or not any(p.search(h) for p in pats):
            continue
        g = v7._grade(h, strong)
        if (a.get("source") in v7._OPINION_SOURCES or v7._DROP.search(h) or v7._ROUNDUP.search(h)
                or v7._DROP_A.search(h) or (v7._SOFT.search(h) and g == 0)):
            continue
        summary = html.unescape(a.get("summary") or "").strip()
        out.append({"id": str(a.get("id")), "ts": a.get("datetime") or 0, "grade": g,
                    "headline": h, "summary": summary, "url": a.get("url"),
                    "source": a.get("source"),
                    "keys": _keys(h + " " + summary[:160], aw)})
    return out


def _keys(text, aw):
    if SPLIT_HYPHEN:
        text = re.sub(r"(?<=\w)[-–—](?=[A-Z])", " ", text)
    return v7._keys(text, aw)


def tag_subs(cls):
    """Before a merge pass: remember each member's original cluster size."""
    for ms in cls:
        for m in ms:
            m["subn"] = len(ms)
    return cls


def seed_clusters(members, window=CLUSTER_SECONDS, min_shared=2, keyf=lambda m: m["keys"]):
    """v7: join the FIRST cluster whose seed shares >= min_shared keys and whose last
    member is within `window`; else open a cluster."""
    cl = []
    for m in members:
        k = keyf(m)
        for c in cl:
            if abs(m["ts"] - c["last_ts"]) <= window and len(k & c["seed"]) >= min_shared:
                c["members"].append(m)
                c["last_ts"] = max(c["last_ts"], m["ts"])
                break
        else:
            cl.append({"seed": k, "last_ts": m["ts"], "members": [m]})
    return [c["members"] for c in cl]


def face(ms):
    if FACE_BY_SUB:
        return max(ms, key=lambda m: (m["grade"], m.get("subn", 1), -m["ts"]))
    return max(ms, key=lambda m: (m["grade"], -m["ts"]))


def rank(cls):
    """Eligible clusters (face grade > 0) in v7 rank order: grade, capped reports, recency."""
    el = [(ms, face(ms)) for ms in cls]
    el = [(ms, f) for ms, f in el if f["grade"]]
    el.sort(key=lambda cf: (cf[1]["grade"], min(len(cf[0]), 5), max(m["ts"] for m in cf[0])),
            reverse=True)
    return el


def finish(cls, k):
    shown = sorted(rank(cls)[:k], key=lambda cf: min(m["ts"] for m in cf[0]), reverse=True)
    return [{"date": datetime.fromtimestamp(min(m["ts"] for m in ms), tz=timezone.utc).date().isoformat(),
             "source": f["source"], "headline": f["headline"], "summary": f["summary"],
             "url": f["url"], "outlets": len(ms), "ids": [m["id"] for m in ms]} for ms, f in shown]
