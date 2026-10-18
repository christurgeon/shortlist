# Dedupe prototypes (2026-10-18)

Every option measured for one-item-per-event in the `/deep` news section. The verdict and the
table are in `docs/audits/2026-10-18-news-event-dedupe.md`; the shipped filter is
`../news_filter_v8.py`.

Run from the repo root. The raw feeds and labels are local-only (third-party text):
`scratch/news_probe_2026-10-16/` and `scratch/news_panel_2026-10-18/accept/`.

```
uv run python docs/audits/scripts/dedupe_2026-10-18/check_v7.py      # opt_seed == frozen v7
uv run python docs/audits/scripts/dedupe_2026-10-18/evaluate.py opt_seed opt_final.recommended opt_sig opt_lex.mp20
uv run python docs/audits/scripts/dedupe_2026-10-18/port_check.py    # production vs the prototype
```

`accept_labels.json` holds hand event tags (ids and short tags only) for the acceptance names.
The LLM option (`opt_llm.py`) reads cached responses from a local `llm_cache/`, which is not
committed because its prompts contain headlines; without it, that option needs live calls
(`DEDUPE_LLM_LIVE=1`). The embeddings option needs `model2vec`, which the repo does not depend on.
