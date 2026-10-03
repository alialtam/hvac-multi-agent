import pytest
from app.llm import LLMService, LLMUnavailable
from app.llm.providers import FakeProvider, ProviderError
from app.agents.schemas import Diagnosis

GOOD = {"cause": "filter_blockage", "cause_text": "Blocked filter", "confidence": 0.8,
        "evidence": ["airflow dropped"], "sources": []}
RULES_OBJ = Diagnosis(cause="unknown", cause_text="rules", confidence=0.5, evidence=["rule"])


def make(openai=None, ollama=None, provider="openai", with_rules=True):
    s = LLMService(provider)
    s.providers = {"openai": FakeProvider(openai or []), "ollama": FakeProvider(ollama or [])}
    if with_rules:
        s.register_rules("diagnosis", lambda ctx: RULES_OBJ)
    return s


def test_first_try_ok():
    obj, meta = make(openai=[GOOD]).complete("p", Diagnosis, "diagnosis")
    assert obj.cause == "filter_blockage" and meta["provider"] == "openai" and meta["attempts"] == 1


def test_retry_once_on_bad_json():
    obj, meta = make(openai=["not json", GOOD]).complete("p", Diagnosis, "diagnosis")
    assert meta["provider"] == "openai" and meta["attempts"] == 2


def test_fenced_json_accepted():
    obj, _ = make(openai=["```json\n" + __import__("json").dumps(GOOD) + "\n```"]).complete(
        "p", Diagnosis, "diagnosis")
    assert obj.confidence == 0.8


def test_fallback_to_other_provider():
    s = make(openai=[ProviderError("x"), ProviderError("x")], ollama=[GOOD])
    obj, meta = s.complete("p", Diagnosis, "diagnosis")
    assert meta["provider"] == "ollama" and "openai" in meta["fallbacks"]


def test_fallback_to_rules():
    s = make(openai=[ProviderError("x")] * 2, ollama=[ProviderError("x")] * 2)
    obj, meta = s.complete("p", Diagnosis, "diagnosis")
    assert meta["provider"] == "rules" and obj.cause_text == "rules"


def test_rules_provider_selected_never_calls_llm():
    s = make(provider="rules")
    _, meta = s.complete("p", Diagnosis, "diagnosis")
    assert meta["provider"] == "rules" and s.providers["openai"].calls == 0


def test_no_rules_registered_raises():
    s = make(provider="rules", with_rules=False)
    with pytest.raises(LLMUnavailable):
        s.complete("p", Diagnosis, "diagnosis")


def test_set_provider_validates():
    s = make()
    s.set_provider("ollama")
    assert s.provider == "ollama"
    with pytest.raises(ValueError):
        s.set_provider("bogus")


def test_log_hook_called():
    seen = []
    s = make(openai=[GOOD])
    s.log_hook = seen.append
    s.complete("p", Diagnosis, "diagnosis")
    assert seen and seen[0]["ok"] and seen[0]["agent"] == "diagnosis"