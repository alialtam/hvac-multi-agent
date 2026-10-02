# LLM layer (Ahmed)

One function every agent uses, so the provider can be switched live from the dashboard.

```python
def complete(prompt: str, schema: type[BaseModel] | None = None, agent: str = "") -> BaseModel | str:
    """Ask the current provider; validate the answer against `schema` (Pydantic).
    On timeout, error or invalid JSON: retry once, then fall back to the next provider."""
```

| Provider | Setting | Notes |
| --- | --- | --- |
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` in `.env` | main provider for the demo |
| `ollama` | `OLLAMA_URL`, `OLLAMA_MODEL` (e.g. `qwen2.5:1.5b`) | offline backup; ask for JSON output |
| `rules` | none | deterministic fallback, so the demo never hangs |

- Current provider: `LLM_PROVIDER` in `.env`, changed at runtime by `POST /settings/llm`
  (`{"provider": "openai" | "ollama" | "rules"}`); the API returns the shape in
  `contracts/api_examples/settings_llm.json`.
- Optional per-agent override (e.g. Supervisor and Diagnosis on OpenAI, Maintenance on a small model).
- Log provider, model, tokens and duration for every call: these go into the execution trace
  and the cost table in the report.
- Tests: use a fake provider that returns fixed answers, so tests run without internet or keys.
