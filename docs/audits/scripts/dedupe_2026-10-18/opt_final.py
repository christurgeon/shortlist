"""Candidate design: v7 seed clusters + ONE non-chaining merge pass.

Clusters are visited oldest first (by first article). A cluster B joins the earliest still-open
cluster A (A has not itself joined another) when REVERSAL(A) == REVERSAL(B) and any of:
  1. cos(centroid A, centroid B) >= COS_MIN and |first A - first B| <= 4 days
     (TF-IDF over headline + summary[:300], idf from this ticker's filtered articles);
  2. both faces name a once-per-window family (results, layoff) and within 4 days;
  3. both faces name a deal family (mna) and share >= 1 key (seed or face keys) within DEAL_DAYS.
Centroids and keys are the clusters' ORIGINAL ones, so a merged cluster never grows its
reach: a story cannot snowball. The face is the strongest member of the largest constituent.
"""
import math
import re
from collections import Counter

import base
import opt_lex

COS_MIN = 0.20
DEAL_DAYS = 7
FAMILIES = {
    "results": re.compile(r"\bq[1-4]\b|quarter|results|earnings|\beps\b", re.I),
    "layoff": re.compile(r"layoffs?|\blay(s|ing)? off\b|job cuts|\bcuts? .{0,20}\bjobs\b|workforce", re.I),
}
DEAL = re.compile(r"acqui|merger|takeover|buyout|\bbids?\b|tie-?up|megadeal|\bbuy\b", re.I)
# A development that changes the story is never folded into the report it follows.
REVERSAL = re.compile(r"\b(den(y|ies|ied)|rules? out|ruled out|walks? away|withdr[ae]w\w*|"
                      r"abandon\w*|terminat\w*|calls? off|scrap\w*|rejects?|rejected|"
                      r"completes|closes|blocks?|blocked)\b", re.I)


class _V:
    def __init__(self, cos_min=COS_MIN, deal_days=DEAL_DAYS, parity=True, deal=True, fams=True,
                 face_by_sub=True, sameday=False, lastwin=False):
        self.cos_min, self.deal_days, self.parity, self.deal, self.fams = \
            cos_min, deal_days, parity, deal, fams
        self.face_by_sub, self.sameday, self.lastwin = face_by_sub, sameday, lastwin

    def merge(self, members, aw):
        cls = base.tag_subs(base.seed_clusters(members))
        vs = {id(m): v for m, v in zip(members, opt_lex._vecs(members, aw))}
        info = []
        for ms in cls:
            acc = Counter()
            for m in ms:
                for w, x in vs[id(m)].items():
                    acc[w] += x
            norm = math.sqrt(sum(x * x for x in acc.values())) or 1.0
            f = max(ms, key=lambda m: (m["grade"], -m["ts"]))
            h = f["headline"]
            info.append({"cent": {w: x / norm for w, x in acc.items()},
                         "first": min(m["ts"] for m in ms),
                         "last": max(m["ts"] for m in ms),
                         "fam": {k for k, p in FAMILIES.items() if p.search(h)} if self.fams else set(),
                         "deal": bool(self.deal and DEAL.search(h)),
                         "keys": f["keys"] | ms[0]["keys"],
                         "rev": bool(REVERSAL.search(h))})
        order = sorted(range(len(cls)), key=lambda i: info[i]["first"])
        target = {}
        for pos, j in enumerate(order):
            b = info[j]
            for i in order[:pos]:
                if target.get(i, i) != i:
                    continue
                a = info[i]
                if self.parity and a["rev"] != b["rev"]:
                    continue
                dt = abs(b["first"] - a["first"])
                if self.lastwin:
                    dt = max(0, b["first"] - a["last"])
                if (dt <= base.CLUSTER_SECONDS and (opt_lex._cos(a["cent"], b["cent"]) >= self.cos_min
                                                    or a["fam"] & b["fam"])) \
                        or (a["deal"] and b["deal"] and a["keys"] & b["keys"]
                            and dt <= self.deal_days * base.DAY) \
                        or (self.sameday and dt <= base.DAY and a["keys"] & b["keys"]):
                    target[j] = i
                    break
        out = {}
        for j, ms in enumerate(cls):
            out.setdefault(target.get(j, j), []).extend(ms)
        return list(out.values())

    def clusters(self, articles, ticker, name):
        return self.merge(base.prefilter(articles, ticker, name), base.alias_words(ticker, name))

    def select(self, articles, ticker, name, k=10):
        old = base.FACE_BY_SUB
        base.FACE_BY_SUB = self.face_by_sub
        try:
            return base.finish(self.clusters(articles, ticker, name), k), {}
        finally:
            base.FACE_BY_SUB = old


final = _V()
no_deal = _V(deal=False)
no_parity = _V(parity=False)
deal4 = _V(deal_days=4)
deal10 = _V(deal_days=10)
deal14 = _V(deal_days=14)
cos_only = _V(deal=False, fams=False)
v7face = _V(face_by_sub=False)
v7face_nodeal = _V(face_by_sub=False, deal=False)
v7face_sameday = _V(face_by_sub=False, sameday=True)
sameday = _V(sameday=True)
v7face_last = _V(face_by_sub=False, lastwin=True)
v7face_last_nodeal = _V(face_by_sub=False, lastwin=True, deal=False)


def clusters(articles, ticker, name):
    return final.clusters(articles, ticker, name)


def select(articles, ticker, name, k=10):
    return final.select(articles, ticker, name, k)
no_cos = _V(face_by_sub=False, cos_min=2.0)
cos15 = _V(face_by_sub=False, cos_min=0.15)
cos25 = _V(face_by_sub=False, cos_min=0.25)
recommended = v7face
