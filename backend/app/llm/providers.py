import json, os


class ProviderError(Exception):
    pass


class OpenAICompatible:
    """OpenAI, Gemini or Groq. Set OPENAI_BASE_URL to switch."""
    name = "openai"

    def available(self) -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def call(self, prompt: str) -> tuple[str, int]:
        from openai import OpenAI
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                        base_url=os.getenv("OPENAI_BASE_URL") or None, timeout=30)
        r = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"}, temperature=0.2)
        return r.choices[0].message.content or "", (r.usage.total_tokens if r.usage else 0)


class OllamaProvider:
    name = "ollama"

    def available(self) -> bool:
        return True  # real check happens on call

    def call(self, prompt: str) -> tuple[str, int]:
        import ollama
        client = ollama.Client(host=os.getenv("OLLAMA_URL", "http://localhost:11434"), timeout=60)
        r = client.chat(model=os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b"),
                        messages=[{"role": "user", "content": prompt}], format="json")
        tokens = (r.get("prompt_eval_count") or 0) + (r.get("eval_count") or 0)
        return r["message"]["content"], tokens


class FakeProvider:
    """For tests and for forcing agentic behaviours. Responses: dict, str or Exception."""
    name = "fake"

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = 0

    def available(self) -> bool:
        return True

    def call(self, prompt: str) -> tuple[str, int]:
        self.calls += 1
        if not self.responses:
            raise ProviderError("fake provider has no more responses")
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return (r if isinstance(r, str) else json.dumps(r)), 0