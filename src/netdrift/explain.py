"""explain.py — natural-language drift explanations (AI1), opt-in and grounded.

Augments, never replaces, the deterministic `diagnose.py` cause table. The
design is the one the pre-launch audit settled on (Pillar IV, AI1):

- **Opt-in and off by default.** With no `NETDRIFT_EXPLAIN_*` env configured,
  `explain()` returns the deterministic `diagnose.py` hint and makes **no network
  call**. The LLM path is reached only when an operator explicitly enables it.
- **Local model by default, bring-your-own-key for hosted.** `ollama` (a local
  model on the operator's own box) is the default provider; an OpenAI-compatible
  endpoint is supported with a key.
- **Read-only and cache-friendly.** This module never touches a database or the
  `GET /drifts` read path. It is deterministic given its inputs, and the
  fingerprint it returns is the intended cache key — a caller (the scheduler, in
  a follow-up) generates once per *structural* drift and stores the result. It is
  never correct to call this on a page-load path.
- **Grounded, with a guaranteed fallback.** The prompt is built from the actual
  intent/reality values plus the `diagnose.py` hints, so the model augments the
  deterministic reasoning rather than inventing it. If the call fails, times out,
  or returns nothing, the deterministic hint is returned instead — the feature
  can never make drift *less* explainable than it already is.

This module is the engine only. Wiring it into the scheduler and persisting the
result in a nullable `explanation` column keyed on fingerprint is a separate,
storage-touching change.
"""

import os

import httpx

from netdrift.diagnose import diagnose
from netdrift.fingerprint import fingerprint as _fingerprint

_DEFAULT_OLLAMA_URL = "http://localhost:11434"
_DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
_DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
_DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
_DEFAULT_TIMEOUT = 20.0

# Provider values that mean "feature disabled".
_OFF = {"", "off", "none", "disabled", "false", "0"}


def _config():
    """Read the explain configuration from the environment (all optional)."""
    return {
        "provider": os.environ.get("NETDRIFT_EXPLAIN_PROVIDER", "off").strip().lower(),
        "model": os.environ.get("NETDRIFT_EXPLAIN_MODEL", "").strip(),
        "url": os.environ.get("NETDRIFT_EXPLAIN_URL", "").strip(),
        "api_key": os.environ.get("NETDRIFT_EXPLAIN_API_KEY", "").strip(),
    }


def _deterministic_text(drift):
    """The offline fallback: the most-likely deterministic cause hint, or a
    neutral line if the drift type has none."""
    hints = diagnose(drift)
    if hints:
        return hints[0]
    return "No deterministic cause hint is available for this drift type."


def build_prompt(drift, co_occurring=None):
    """Build the grounding prompt for one drift record.

    Includes the concrete intent/reality values, platform, and the deterministic
    `diagnose.py` hints as grounding, plus a short summary of co-occurring drift
    on the same device (which often reveals a shared root cause, e.g. a bulk
    shut). Asks for one concise plain-English hint — no markdown, no fixes.
    """
    object_type = drift["object"].split(":")[0]
    hints = diagnose(drift)
    grounding = "\n".join(f"- {h}" for h in hints) or "- (no deterministic hint)"

    lines = [
        "You are a senior network engineer explaining one configuration-drift "
        "finding to a colleague. In one or two plain sentences, give the single "
        "most likely root cause. Be specific to the values shown. Do not suggest "
        "a fix, do not use markdown, do not restate the question.",
        "",
        f"Device: {drift.get('device', '(unknown)')}  Platform: "
        f"{drift.get('platform', '(unknown)')}",
        f"Object: {drift['object']}  ({object_type})",
        f"Field: {drift['field']}",
        f"Drift kind: {drift['drift_kind']}  Severity: {drift.get('severity', '?')}",
        f"Intended (NetBox): {drift['intent']!r}",
        f"Actual (device):   {drift['reality']!r}",
        "",
        "Known typical causes for this kind of drift:",
        grounding,
    ]

    if co_occurring:
        summary = ", ".join(
            f"{d['object']}/{d['field']}" for d in co_occurring
        )
        lines += ["", f"Other drift detected on this device right now: {summary}"]

    return "\n".join(lines)


def _deterministic_remediation_text(rendered_commands, dry_run_diff):
    """Offline fallback for a remediation summary: a factual command count.

    No model needed — counts the non-empty command lines so the operator at
    least sees the size of the change without an LLM.
    """
    lines = [ln for ln in (rendered_commands or "").splitlines() if ln.strip()]
    n = len(lines)
    if n == 0:
        return "This fix would make no configuration change."
    if n == 1:
        return f"This fix would run 1 configuration command: {lines[0].strip()}"
    return f"This fix would run {n} configuration commands on the device."


def build_remediation_prompt(rendered_commands, dry_run_diff, drift=None):
    """Build the grounding prompt for an AI2 remediation summary.

    Grounded in the actual rendered commands and the live dry-run diff. Asks for
    a factual plain-English summary of *what the change does* — never advice on
    whether to apply it, which stays a human decision.
    """
    lines = [
        "You are a senior network engineer. In one or two plain sentences, "
        "summarize what the following configuration change would do to the "
        "device. Be factual and specific to the commands shown. Do NOT advise "
        "whether to apply it, do not use markdown, do not restate the question.",
        "",
    ]
    if drift:
        lines.append(
            f"It is the proposed fix for drift on {drift.get('object', '?')} "
            f"field {drift.get('field', '?')} ({drift.get('drift_kind', '?')})."
        )
        lines.append("")
    lines += ["Rendered commands:", (rendered_commands or "").strip() or "(none)"]
    if dry_run_diff and dry_run_diff.strip():
        lines += ["", "Dry-run diff:", dry_run_diff.strip()]
    return "\n".join(lines)


def summarize_remediation(rendered_commands, dry_run_diff, *, drift=None, llm=None):
    """Return a natural-language summary of what a remediation would do (AI2).

    Same opt-in / off-by-default / grounded-with-fallback contract as
    ``explain()``, but for a fix rather than a drift. Generated at dry-run time
    (an explicit, infrequent user action), so it is not cached — the diff is
    live. Returns ``{"text", "source"}`` where source is ``"llm"`` or
    ``"deterministic"``. ``llm`` is injectable for tests.
    """
    fallback = _deterministic_remediation_text(rendered_commands, dry_run_diff)

    if llm is None:
        llm = _resolve_llm(_config())
    if llm is None:
        return {"text": fallback, "source": "deterministic"}

    prompt = build_remediation_prompt(rendered_commands, dry_run_diff, drift=drift)
    try:
        text = llm(prompt)
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        return {"text": fallback, "source": "deterministic"}

    return {"text": text, "source": "llm"}


def _call_ollama(prompt, cfg):
    base = (cfg["url"] or _DEFAULT_OLLAMA_URL).rstrip("/")
    model = cfg["model"] or _DEFAULT_OLLAMA_MODEL
    resp = httpx.post(
        f"{base}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json().get("response", "")


def _call_openai(prompt, cfg):
    base = (cfg["url"] or _DEFAULT_OPENAI_URL).rstrip("/")
    model = cfg["model"] or _DEFAULT_OPENAI_MODEL
    headers = {"Authorization": f"Bearer {cfg['api_key']}"} if cfg["api_key"] else {}
    resp = httpx.post(
        f"{base}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        },
        timeout=_DEFAULT_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


def _resolve_llm(cfg):
    """Return a callable(prompt) -> str for the configured provider, or None when
    the feature is off or the provider is unknown."""
    provider = cfg["provider"]
    if provider in _OFF:
        return None
    if provider == "ollama":
        return lambda prompt: _call_ollama(prompt, cfg)
    if provider in ("openai", "openai-compatible"):
        return lambda prompt: _call_openai(prompt, cfg)
    return None


def explain(drift, co_occurring=None, *, llm=None):
    """Return a natural-language explanation for one drift record.

    Args:
        drift: a drift record (the dict differ.diff() emits, with device/platform
            stamped on as the pipeline does).
        co_occurring: optional list of other drift records on the same device,
            used as extra grounding context.
        llm: optional callable(prompt) -> str, injectable for tests. When None,
            it is resolved from the environment; if the feature is off, the
            deterministic fallback is returned without any network call.

    Returns a dict ``{"fingerprint", "explanation", "source"}`` where ``source``
    is ``"llm"`` or ``"deterministic"``. The fingerprint is the intended cache
    key — callers should generate once per fingerprint and store the result.
    """
    fingerprint = _fingerprint(drift)
    fallback = _deterministic_text(drift)

    if llm is None:
        llm = _resolve_llm(_config())
    if llm is None:
        return {"fingerprint": fingerprint, "explanation": fallback,
                "source": "deterministic"}

    prompt = build_prompt(drift, co_occurring)
    try:
        text = llm(prompt)
    except Exception:
        text = ""
    text = (text or "").strip()
    if not text:
        return {"fingerprint": fingerprint, "explanation": fallback,
                "source": "deterministic"}

    return {"fingerprint": fingerprint, "explanation": text, "source": "llm"}
