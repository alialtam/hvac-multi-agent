import json, logging, os, re, time
from typing import Callable, Optional
from dotenv import load_dotenv
from pydantic import BaseModel
from .providers import OpenAICompatible, OllamaProvider, FakeProvider, ProviderError  # noqa: F401

load_dotenv()
log = logging.getLogger("llm")


class LLMUnavailable(Exception):
    pass


def _extract_json(raw: str) -> str:
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    a, b = raw.find("{"), raw.rfind("}")
    return raw[a:b + 1] if a != -1 and b > a else raw


class LLMService:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider or os.getenv("LLM_PROVIDER", "openai")
        self.providers = {"openai": OpenAICompatible(), "ollama": OllamaProvider()}
        self.rules: dict[str, Callable[[dict], BaseModel]] = {}   # agent -> rules fn
        self.log_hook: Optional[Callable[[dict], None]] = None    # trace/DB logger

    # settings
    def available_providers(self) -> list[str]:
        return ["openai", "ollama", "rules"]

    def set_provider(self, name: str) -> None:
        if name not in self.available_providers() and name not in self.providers:
            raise ValueError(f"unknown provider {name}")
        self.provider = name

    def register_rules(self, agent: str, fn: Callable[[dict], BaseModel]) -> None:
        self.rules[agent] = fn

    def _chain(self) -> list[str]:
        if self.provider == "rules":
            return ["rules"]
        rest = [k for k in self.providers if k != self.provider]
        return [self.provider] + rest + ["rules"]

    def _log(self, **rec) -> None:
        log.info("llm %s", rec)
        if self.log_hook:
            try:
                self.log_hook(rec)
            except Exception:
                log.exception("log_hook failed")

    def complete(self, prompt: str, schema: type[BaseModel], agent: str,
                 context: Optional[dict] = None) -> tuple[BaseModel, dict]:
        """Returns (validated object, meta). Chain: chosen provider (retry once)
        -> other providers -> rules. Raises LLMUnavailable only if rules also missing."""
        meta = {"agent": agent, "provider": None, "tokens": 0, "ms": 0,
                "attempts": 0, "fallbacks": []}
        full = prompt + "\n\nReply with JSON only, matching this schema:\n" + \
            json.dumps(schema.model_json_schema())
        for name in self._chain():
            if name == "rules":
                return self._run_rules(agent, schema, context, meta)
            p = self.providers.get(name)
            if p is None or not p.available():
                meta["fallbacks"].append(name)
                self._log(agent=agent, provider=name, ok=False, error="not available")
                continue
            for attempt in (1, 2):
                meta["attempts"] += 1
                t0 = time.perf_counter()
                try:
                    raw, tokens = p.call(full)
                    obj = schema.model_validate_json(_extract_json(raw))
                except Exception as e:
                    ms = int((time.perf_counter() - t0) * 1000)
                    self._log(agent=agent, provider=name, attempt=attempt, ok=False,
                              ms=ms, error=str(e)[:200])
                    continue
                ms = int((time.perf_counter() - t0) * 1000)
                meta.update(provider=name, tokens=tokens, ms=ms)
                self._log(agent=agent, provider=name, attempt=attempt, ok=True,
                          ms=ms, tokens=tokens)
                return obj, meta
            meta["fallbacks"].append(name)
        raise LLMUnavailable(f"no provider worked for {agent}")  # unreachable: chain ends in rules

    def _run_rules(self, agent, schema, context, meta):
        fn = self.rules.get(agent)
        if fn is None:
            raise LLMUnavailable(f"no rules fallback registered for agent '{agent}'")
        t0 = time.perf_counter()
        obj = schema.model_validate(fn(context or {}).model_dump())
        meta.update(provider="rules", ms=int((time.perf_counter() - t0) * 1000))
        self._log(agent=agent, provider="rules", ok=True, ms=meta["ms"])
        return obj, meta


llm = LLMService()
complete = llm.complete