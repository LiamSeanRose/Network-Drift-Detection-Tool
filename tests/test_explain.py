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


# --- AI3 known-issue suggestions ---------------------------------------------

def test_suggest_off_by_default_returns_deterministic(monkeypatch):
    for var in ("NETDRIFT_EXPLAIN_PROVIDER", "NETDRIFT_EXPLAIN_MODEL",
                "NETDRIFT_EXPLAIN_URL", "NETDRIFT_EXPLAIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    def explode(*a, **k):
        raise AssertionError("must not call the network when off")

    monkeypatch.setattr(explain.httpx, "post", explode)
    result = explain.suggest_known_issue(_drift())
    assert result["source"] == "deterministic"
    assert result["cause"]
    assert result["fix"]


def test_suggest_deterministic_fix_keys_on_drift_kind():
    missing_reality = _drift(object_ref="interface:Ethernet9", field="_interface",
                             drift_kind="missing_in_reality")
    assert "device to match NetBox" in explain._deterministic_fix_text(missing_reality)
    undoc = _drift(object_ref="vlan:99", field="_vlan", drift_kind="missing_in_intent")
    assert "Document" in explain._deterministic_fix_text(undoc)


def test_suggest_uses_llm_when_it_returns_both_lines():
    def fake_llm(prompt):
        return "CAUSE: A maintenance window left it shut.\nFIX: Re-enable the interface."

    result = explain.suggest_known_issue(_drift(), llm=fake_llm)
    assert result["source"] == "llm"
    assert result["cause"] == "A maintenance window left it shut."
    assert result["fix"] == "Re-enable the interface."


def test_suggest_falls_back_when_llm_output_unparseable():
    # Missing the FIX line -> deterministic pair, not a half-filled suggestion.
    result = explain.suggest_known_issue(_drift(), llm=lambda p: "CAUSE: only a cause")
    assert result["source"] == "deterministic"
    assert result["fix"]


def test_suggest_falls_back_when_llm_raises():
    def boom(p):
        raise RuntimeError("down")

    assert explain.suggest_known_issue(_drift(), llm=boom)["source"] == "deterministic"


def test_parse_suggestion_is_case_insensitive():
    cause, fix = explain._parse_suggestion("cause: lower\nFix: mixed")
    assert cause == "lower"
    assert fix == "mixed"


# --- AI2 remediation summaries ----------------------------------------------

_COMMANDS = "interface Ethernet1\n  no shutdown\n"
_DIFF = "+  no shutdown"


def test_remediation_off_by_default_returns_deterministic(monkeypatch):
    for var in ("NETDRIFT_EXPLAIN_PROVIDER", "NETDRIFT_EXPLAIN_MODEL",
                "NETDRIFT_EXPLAIN_URL", "NETDRIFT_EXPLAIN_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    def explode(*a, **k):
        raise AssertionError("must not call the network when off")

    monkeypatch.setattr(explain.httpx, "post", explode)
    result = explain.summarize_remediation(_COMMANDS, _DIFF)
    assert result["source"] == "deterministic"
    assert "command" in result["text"].lower()


def test_remediation_deterministic_counts_commands():
    # Two non-empty command lines -> a count summary.
    text = explain._deterministic_remediation_text(_COMMANDS, _DIFF)
    assert "2 configuration commands" in text


def test_remediation_deterministic_single_command_shows_it():
    text = explain._deterministic_remediation_text("vlan 99", "")
    assert "1 configuration command" in text
    assert "vlan 99" in text


def test_remediation_deterministic_empty_is_no_change():
    assert "no configuration change" in explain._deterministic_remediation_text("", "")


def test_remediation_injected_llm_is_used():
    result = explain.summarize_remediation(
        _COMMANDS, _DIFF, llm=lambda p: "Re-enables Ethernet1.")
    assert result["source"] == "llm"
    assert result["text"] == "Re-enables Ethernet1."


def test_remediation_llm_failure_falls_back():
    def boom(p):
        raise RuntimeError("down")

    result = explain.summarize_remediation(_COMMANDS, _DIFF, llm=boom)
    assert result["source"] == "deterministic"


def test_remediation_prompt_includes_commands_and_diff():
    prompt = explain.build_remediation_prompt(
        _COMMANDS, _DIFF, drift={"object": "interface:Ethernet1",
                                 "field": "enabled", "drift_kind": "value_mismatch"})
    assert "no shutdown" in prompt
    assert "Dry-run diff" in prompt
    assert "interface:Ethernet1" in prompt


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
