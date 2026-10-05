# Knowledge base (Abdul)

Short documents the Diagnosis and Maintenance agents search with a tool.
Manuals and past incidents are synthetic, written for this project.

```
knowledge/
  manuals/       # 10 Markdown notes: fault types, components, safety, tariff, baselines
  incidents/     # 20 past incident records (fictional, one file each)
  search.py      # search_knowledge(query, k=3) -> list of {id, title, source, text, score}
  rules.py       # diagnose_rules(event) -> Diagnosis (offline fallback, never reads fault_label)
  __init__.py    # registers diagnose_rules as the "diagnosis" rules fallback in the LLM layer
```

- Manual note: symptoms, action to take, and baseline values.
- Incident record: date, unit, cause, symptoms seen, resolution.
- Search: BM25 (`rank-bm25`), no model needed for ~30 short documents.
- Tests: `backend/tests/test_knowledge.py` covers search, each rules cause, a real AHU-1 event,
  the LLM fallback to rules, and a check that `fault_label` is never read.
