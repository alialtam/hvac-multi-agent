# Knowledge base (Abdulelah)

Short documents the Diagnosis and Maintenance agents search with a tool.

```
knowledge/
  manuals/       # ~10 Markdown notes: one per fault type and component
  incidents/     # ~20 past incident records (realistic, clearly fictional)
  search.py      # search_knowledge(query, k=3) -> list of {id, title, text, score}
```

- Each manual note: symptoms, likely causes, how to confirm, repair steps, typical downtime.
  Use the measured fault signatures in `docs/HANDOVER_PERSON2.md` (section 3) so the notes
  match what the simulator actually produces.
- Each incident record: date, unit, symptoms seen, confirmed cause, fix, downtime.
- Search: BM25 (`pip install rank-bm25`) is enough for ~30 short documents and needs no model.
- Tests: a query like "airflow dropped, compressor power rose" returns the filter-blockage note first.
