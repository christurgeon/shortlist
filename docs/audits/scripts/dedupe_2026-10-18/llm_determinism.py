"""Nondeterminism of the LLM option: tickers whose shown items (sets of article ids) differ
between two samples of the same prompt. Usage: llm_determinism.py opt_llm.group opt_llm.group_r1"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate as E  # noqa: E402
from hidden import resolve  # noqa: E402

a, b = resolve(sys.argv[1]), resolve(sys.argv[2])
jobs = [(t, n, arts) for t, n, arts, _ in E.labelled()] + \
       [(t, n, E.load(f"{E.ACCEPT}/{t}.raw.json")) for t, n in E.ACCEPT_NAMES.items()]
diff = []
for t, n, arts in jobs:
    x = sorted(tuple(sorted(i["ids"])) for i in a.select(arts, t, n, E.K)[0])
    y = sorted(tuple(sorted(i["ids"])) for i in b.select(arts, t, n, E.K)[0])
    if x != y:
        diff.append(f"{t}({len(set(x) ^ set(y))})")
print(f"{sys.argv[1]} vs {sys.argv[2]}: {len(diff)}/{len(jobs)} briefs differ:", " ".join(diff))
