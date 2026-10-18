"""Option 2: event-signature merge over v7's seed clusters.

A cluster's signature is the set of event families its FACE headline names, plus the face's
keys. One pass, oldest first: cluster B joins the earliest open cluster A when their first
articles are within WINDOW, they share a family, and either the family is once-per-window
(results, layoff) or A's and B's face keys share >= 1 key (a counterparty, product or person).
A target is compared on its ORIGINAL face only, so a merge cannot snowball.
"""
import re

import base

WINDOW = 4 * base.DAY
FAMILIES = {
    "mna": r"acqui|merger|takeover|buyout|\bbids?\b|tie-?up|megadeal|\bbuy\b",
    "layoff": r"layoffs?|\blay(s|ing)? off\b|job cuts|\bcuts? .{0,20}\bjobs\b|workforce",
    "results": r"\bq[1-4]\b|quarter|results|earnings|\beps\b",
    "approval": r"approv|\bfda\b|clearance|\bce mark\b|breakthrough|designation",
    "trial": r"\bphase (i|ii|iii|1|2|3)\b|\btrial|\bstudy\b|\bdata\b|endpoint",
    "legal": r"lawsuit|\bsues?\b|\bsued\b|class action|litigation|\bcourt\b|settle|\bfines?\b|probe|investigat",
    "exec": r"\b(ceo|cfo|coo|chief|chair\w*|president)\b|appoint|\bnamed?\b|resign|steps? down",
    "rating": r"upgrade|downgrade|initiat|price target|overweight|underweight|outperform",
    "contract": r"contract|\border\b|award|agreement|\bdeal\b|partnership|licen[cs]e",
    "capital": r"buyback|repurchase|offering|convertible|\bnotes\b|dividend",
}
_FAM = {k: re.compile(v, re.I) for k, v in FAMILIES.items()}
KEYLESS = {"results", "layoff"}


def families(headline):
    return {k for k, p in _FAM.items() if p.search(headline)}


def merge(cls, keyless=KEYLESS, window=WINDOW):
    base.tag_subs(cls)
    sig = []
    for ms in cls:
        f = base.face(ms)
        sig.append((families(f["headline"]), f["keys"], min(m["ts"] for m in ms)))
    order = sorted(range(len(cls)), key=lambda i: sig[i][2])
    target = {}
    for pos, j in enumerate(order):
        fj, kj, tj = sig[j]
        for i in order[:pos]:
            if target.get(i, i) != i:
                continue
            fi, ki, ti = sig[i]
            shared = fi & fj
            if abs(tj - ti) <= window and shared and (shared & keyless or ki & kj):
                target[j] = i
                break
    out = {}
    for j, ms in enumerate(cls):
        out.setdefault(target.get(j, j), []).extend(ms)
    return list(out.values())


def clusters(articles, ticker, name):
    return merge(base.seed_clusters(base.prefilter(articles, ticker, name)))


def select(articles, ticker, name, k=10):
    return base.finish(clusters(articles, ticker, name), k), {}


class _V:
    def __init__(self, **kw):
        self.kw = kw

    def clusters(self, articles, ticker, name):
        return merge(base.seed_clusters(base.prefilter(articles, ticker, name)), **self.kw)

    def select(self, articles, ticker, name, k=10):
        return base.finish(self.clusters(articles, ticker, name), k), {}


keyed = _V(keyless=set())          # every family needs a shared key
w2 = _V(window=2 * base.DAY)
