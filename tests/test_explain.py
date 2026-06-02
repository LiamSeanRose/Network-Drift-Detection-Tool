"""tests/test_explain.py — AI1 drift-explanation engine.

Every test is network-free: the LLM callable is injected, or the feature is off
by default. The deterministic diagnose.py fallback is the safety net under all of
it, so explain() always returns usable text.
"""

from netdrift import explain
from netdrift.fingerprint import fingerprint


def _drift(object_ref="tunnel:Tunnel0", field="tunnel_state",
           drift_kind="value_mismatch", **extra):
    base = {
        "device": "core-sw-01",
        "platform": "arista_eos",
        "object": object_ref,
        "field": field,
        "intent": "up",
        "reality": "down",
        "drift_kind": drift_kind,
        "severity": "critical",
    }
    base.update(extra)
    return base


# --- off by default ----------------------------------------------------------

def test_off_by_default_returns_deterministic(monkeypatch):
    # No NETDRIFT_EXPLAIN_* env -> deterministic, and crucially no network call.
    for var in ("NETDRIFT_EXPLAIN_PROVIDER", "NETDRIFT_EXPLAIN_MODEL",
                "NETDRIFT_EXPLAIN_URL", "NETDRIFT_EXPLAIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    def explode(*a, **k):
        raise AssertionError("explain must not call the network when off")

    monkeypatch.setattr(explain.httpx, "post", explode)

    result = explain.explain(_drift())
    assert result["source"] == "deterministic"
    assert result["explanation"]
    assert result["fingerprint"] == fingerprint(_drift())


def test_provider_off_value_disables(monkeypatch):
    monkeypatch.setenv("NETDRIFT_EXPLAIN_PROVIDER", "off")
    assert explain._resolve_llm(explain._config()) is None


def test_unknown_provider_disables(monkeypatch):
    monkeypatch.setenv("NETDRIFT_EXPLAIN_PROVIDER", "skynet")
    assert explain._resolve_llm(explain._config()) is None


# --- injected llm path -------------------------------------------------------

def test_injected_llm_text_is_used():
    result = explain.explain(_drift(), llm=lambda prompt: "Likely a bulk shut.")
    assert result["source"] == "llm"
    assert result["explanation"] == "Likely a bulk shut."


def test_llm_failure_falls_back_to_deterministic():
    def boom(prompt):
        raise RuntimeError("model unreachable")

    result = explain.explain(_drift(), llm=boom)
    assert result["source"] == "deterministic"
    assert result["explanation"]  # the diagnose hint, not an error


def test_llm_empty_response_falls_back():
    result = explain.explain(_drift(), llm=lambda prompt: "   ")
    assert result["source"] == "deterministic"


def test_llm_text_is_stripped():
    result = explain.explain(_drift(), llm=lambda prompt: "  trimmed  ")
    assert result["explanation"] == "trimmed"


def test_fingerprint_is_the_cache_key():
    drift = _drift()
    result = explain.explain(drift, llm=lambda p: "x")
    assert result["fingerprint"] == fingerprint(drift)


# --- prompt grounding --------------------------------------------------------

def test_prompt_includes_values_and_grounding():
    prompt = explain.build_prompt(_drift())
    assert "core-sw-01" in prompt
    assert "arista_eos" in prompt
    assert "tunnel:Tunnel0" in prompt
    assert "up" in prompt and "down" in prompt
    # A deterministic diagnose hint must be present as grounding.
    assert "line protocol" in prompt.lower() or "underlay" in prompt.lower()


def test_prompt_includes_co_occurring_context():
    other = _drift(object_ref="interface:Ethernet1", field="enabled")
    prompt = explain.build_prompt(_drift(), co_occurring=[other])
    assert "interface:Ethernet1/enabled" in prompt


def test_prompt_handles_drift_with_no_hint():
    # A field with no diagnose entry still builds a prompt (no crash).
    prompt = explain.build_prompt(_drift(field="nonexistent"))
    assert "(no deterministic hint)" in prompt


# --- provider resolution (no network) ----------------------------------------

def test_ollama_provider_resolves_to_callable(monkeypatch):
    monkeypatch.setenv("NETDRIFT_EXPLAIN_PROVIDER", "ollama")
    llm = explain._resolve_llm(explain._config())
    assert callable(llm)


def test_openai_provider_resolves_to_callable(monkeypatch):
    monkeypatch.setenv("NETDRIFT_EXPLAIN_PROVIDER", "openai")
    llm = explain._resolve_llm(explain._config())
    assert callable(llm)


def test_ollama_call_posts_to_generate_endpoint(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"response": "ollama says hi"}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return FakeResp()

    monkeypatch.setattr(explain.httpx, "post", fake_post)
    out = explain._call_ollama("prompt text", {"url": "", "model": ""})
    assert out == "ollama says hi"
    assert captured["url"].endswith("/api/generate")
    assert captured["json"]["stream"] is False


def test_openai_call_sends_bearer_key(monkeypatch):
    captured = {}

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "openai reply"}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        return FakeResp()

    monkeypatch.setattr(explain.httpx, "post", fake_post)
    out = explain._call_openai("prompt", {"url": "", "model": "", "api_key": "sk-test"})
    assert out == "openai reply"
    assert captured["url"].endswith("/chat/completions")
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
