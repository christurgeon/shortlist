"""Option 1 variants: better lexical clustering. Each variant is a Variant object with
`clusters` and `select`; evaluate.py addresses them as opt_lex.<name>."""
import math
import re
from collections import Counter

import base

_EN_STOP = frozenset("""a about above after again against all also am an and any are as at be because been
before being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers him his how i if in into is it its itself just more most
my no nor not now of off on once only or other our out over own same she should so some such than that
the their them then there these they this those through to too under until up very was we were what
when where which while who whom why will with would you your says said new stock stocks shares share
inc corp company report reports reported according week today year years first one two three per
million billion percent amid after over its may could likely""".split())


class Variant:
    def __init__(self, fn):
        self.fn = fn

    def clusters(self, articles, ticker, name):
        return self.fn(base.prefilter(articles, ticker, name), base.alias_words(ticker, name))

    def select(self, articles, ticker, name, k=10):
        return base.finish(self.clusters(articles, ticker, name), k), {}


def _uf(members, min_shared, window=base.CLUSTER_SECONDS):
    """Union-find: link two articles within `window` sharing >= min_shared keys."""
    parent = list(range(len(members)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i, a in enumerate(members):
        for j in range(i):
            b = members[j]
            if abs(a["ts"] - b["ts"]) <= window and len(a["keys"] & b["keys"]) >= min_shared:
                parent[find(i)] = find(j)
    groups = {}
    for i, m in enumerate(members):
        groups.setdefault(find(i), []).append(m)
    return list(groups.values())


def _sumkeys(members, aw, min_shared):
    """v7 seed rule, keys from headline + the whole summary."""
    def keyf(m):
        return base.v7._keys(m["headline"] + " " + m["summary"], aw)
    return base.seed_clusters(members, min_shared=min_shared, keyf=keyf)


def _tokens(m, aw, summary_chars=300):
    text = (m["headline"] + " " + m["summary"][:summary_chars]).lower()
    return [w for w in re.findall(r"[a-z0-9$%][a-z0-9$%.'-]*[a-z0-9%]", text)
            if w not in _EN_STOP and w not in aw and len(w) >= 3]


def _vecs(members, aw):
    docs = [Counter(_tokens(m, aw)) for m in members]
    df = Counter(w for d in docs for w in d)
    n = len(docs)
    out = []
    for d in docs:
        v = {w: (1 + math.log(c)) * math.log((1 + n) / (1 + df[w])) for w, c in d.items()}
        norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
        out.append({w: x / norm for w, x in v.items()})
    return out


def _cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(x * b.get(w, 0.0) for w, x in a.items())


def _tfidf_seed(members, aw, thr):
    """Join the first cluster within the window whose SEED is TF-IDF-cosine >= thr, or whose
    seed shares >= 2 keys (v7's rule is kept as a sufficient condition)."""
    vs = _vecs(members, aw)
    cl = []
    for m, v in zip(members, vs):
        for c in cl:
            if abs(m["ts"] - c["last_ts"]) <= base.CLUSTER_SECONDS and (
                    len(m["keys"] & c["seed"]) >= 2 or _cos(v, c["sv"]) >= thr):
                c["members"].append(m)
                c["last_ts"] = max(c["last_ts"], m["ts"])
                break
        else:
            cl.append({"seed": m["keys"], "sv": v, "last_ts": m["ts"], "members": [m]})
    return [c["members"] for c in cl]


def _merge_pass(members, aw, thr):
    """v7 seed clusters, then one merge pass: a cluster joins the earliest cluster whose first
    article is within the window and whose ORIGINAL centroid is cosine >= thr to its own.
    Targets keep their original centroid, so a merge cannot snowball."""
    cls = base.tag_subs(base.seed_clusters(members))
    vs = {id(m): v for m, v in zip(members, _vecs(members, aw))}

    def centroid(ms):
        acc = Counter()
        for m in ms:
            for w, x in vs[id(m)].items():
                acc[w] += x
        norm = math.sqrt(sum(x * x for x in acc.values())) or 1.0
        return {w: x / norm for w, x in acc.items()}
    cents = [centroid(ms) for ms in cls]
    firsts = [min(m["ts"] for m in ms) for ms in cls]
    target = list(range(len(cls)))
    for j in range(len(cls)):
        for i in range(j):
            if target[i] == i and abs(firsts[j] - firsts[i]) <= base.CLUSTER_SECONDS \
                    and _cos(cents[i], cents[j]) >= thr:
                target[j] = i
                break
    out = {}
    for j, ms in enumerate(cls):
        out.setdefault(target[j], []).extend(ms)
    return list(out.values())


uf2 = Variant(lambda ms, aw: _uf(ms, 2))
uf3 = Variant(lambda ms, aw: _uf(ms, 3))
sum2 = Variant(lambda ms, aw: _sumkeys(ms, aw, 2))
sum3 = Variant(lambda ms, aw: _sumkeys(ms, aw, 3))
tf20 = Variant(lambda ms, aw: _tfidf_seed(ms, aw, 0.20))
tf30 = Variant(lambda ms, aw: _tfidf_seed(ms, aw, 0.30))
tf40 = Variant(lambda ms, aw: _tfidf_seed(ms, aw, 0.40))
mp20 = Variant(lambda ms, aw: _merge_pass(ms, aw, 0.20))
mp30 = Variant(lambda ms, aw: _merge_pass(ms, aw, 0.30))
mp40 = Variant(lambda ms, aw: _merge_pass(ms, aw, 0.40))
mp15 = Variant(lambda ms, aw: _merge_pass(ms, aw, 0.15))
mp25 = Variant(lambda ms, aw: _merge_pass(ms, aw, 0.25))
