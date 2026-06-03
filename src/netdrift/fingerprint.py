"""fingerprint.py — the drift identity used for known-issue / pattern matching.

``fingerprint()`` is the canonical exact key (object_type|field|drift_kind) every
matching path uses today. The v5.0 fuzzy layer below is purely additive and is
gated by ``FUZZY_MATCHING_ENABLED`` (default off): with the flag unset nothing
here changes the exact-match behaviour, so existing matching is unaffected.

The fuzzy approach is deterministic template normalization (no corpus needed to
implement) plus Jaccard similarity as a tie-break, per the v5.0 roadmap.
Embeddings/TF-IDF were rejected there: they break the self-hosted 2-vCPU promise
and mismodel structured key-value drift.
"""

import os
import re


def fingerprint(drift: dict) -> str:
    """Return a stable string key for a drift record, stripping variable parts.

    Strips: device name, specific object identifier (IP, interface name, VLAN ID),
    intent/reality values, and timestamp. Keeps: object type, field, drift kind.
    This means the same structural problem on any device or identifier maps to
    the same fingerprint and can be matched against a stored known issue.
    """
    object_type = drift["object"].split(":")[0]
    return f"{object_type}|{drift['field']}|{drift['drift_kind']}"


# ---------------------------------------------------------------------------
# v5.0 — fuzzy / semantic fingerprint matching (feature-flagged, default off)
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on"}

# Default similarity at/above which a fuzzy match is accepted. Tuned against the
# labeled corpus in v5.0 sign-off; this is the implementation default.
DEFAULT_FUZZY_THRESHOLD = 0.7


def fuzzy_matching_enabled() -> bool:
    """True when fuzzy matching is turned on (``FUZZY_MATCHING_ENABLED``).

    Off by default; the exact fingerprint remains the only matching key until an
    operator opts in. Read per-call so it can be toggled without a restart."""
    return os.environ.get("FUZZY_MATCHING_ENABLED", "").strip().lower() in _TRUTHY


def fuzzy_threshold() -> float:
    """The accept threshold for a fuzzy match (``FUZZY_MATCH_THRESHOLD``).

    Defaults to ``DEFAULT_FUZZY_THRESHOLD``; an operator tunes it against their
    labeled corpus (see ``driftcheck eval-fuzzy``). Falls back to the default on a
    missing or unparseable value."""
    raw = os.environ.get("FUZZY_MATCH_THRESHOLD", "").strip()
    if not raw:
        return DEFAULT_FUZZY_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        return DEFAULT_FUZZY_THRESHOLD


# Per-object-type identifier normalization. The identifier (the part after the
# first ":") is the variable bit; replacing it with a class token lets the same
# structural drift match across devices, peers, VLANs, and interfaces.
_IDENTIFIER_TOKEN = {
    "bgp_neighbor": "{IP}",
    "ospf_adjacency": "{ROUTER_ID}",
    "vlan": "{VLAN_ID}",
}

# Interface-like identifiers keep their alpha class (Ethernet vs Loopback vs
# Tunnel is a meaningful structural difference) but drop the slot/port numbers.
_SLOT_NUMERIC = re.compile(r"[\d/.\-:]+")


def normalize_object_ref(object_ref: str) -> str:
    """Template-normalize an ``object`` identifier to a class token.

    ``bgp_neighbor:10.0.0.2`` -> ``bgp_neighbor:{IP}``;
    ``vlan:10`` -> ``vlan:{VLAN_ID}``;
    ``interface:TenGigabitEthernet1/1/4`` -> ``interface:TenGigabitEthernet``;
    ``tunnel:Tunnel0`` -> ``tunnel:Tunnel``; ``config``/``device`` unchanged.
    """
    object_type, sep, identifier = object_ref.partition(":")
    if not sep:
        return object_ref  # config / device — no identifier
    if object_type in _IDENTIFIER_TOKEN:
        return f"{object_type}:{_IDENTIFIER_TOKEN[object_type]}"
    if object_type in ("interface", "tunnel"):
        # Strip slot/port numbers, keep the interface class (and collapse a now-
        # empty result to a generic token).
        cls = _SLOT_NUMERIC.sub("", identifier).strip()
        return f"{object_type}:{cls or '{IFACE}'}"
    # Unknown type with an identifier: tokenize it generically so it still
    # normalizes rather than carrying a device-specific value.
    return f"{object_type}:{{ID}}"


def fuzzy_fingerprint(drift: dict) -> str:
    """The normalized fingerprint: like ``fingerprint()`` but with the object
    identifier template-normalized instead of dropped, so Jaccard can compare
    structurally-similar patterns component by component."""
    return (
        f"{normalize_object_ref(drift['object'])}"
        f"|{drift['field']}|{drift['drift_kind']}"
    )


def _tokens(fp: str) -> set[str]:
    """Split a fingerprint into its structural components for Jaccard. Splits on
    the ``|`` and ``:`` separators only (not ``_``), so ``session_state`` stays a
    single token rather than fragmenting into ``session``/``state``."""
    return {t for t in re.split(r"[|:]", fp) if t}


def fuzzy_similarity(fp_a: str, fp_b: str) -> float:
    """Jaccard similarity (0.0–1.0) over two fingerprints' components.

    Identical fingerprints score 1.0; a pair differing in one of four components
    scores 0.6; fully disjoint scores 0.0."""
    if fp_a == fp_b:
        return 1.0
    a, b = _tokens(fp_a), _tokens(fp_b)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def fuzzy_match(target_fp: str, candidate_fps, *, threshold=DEFAULT_FUZZY_THRESHOLD):
    """Find the candidate fingerprint most similar to ``target_fp``.

    Returns ``(best_fingerprint, confidence)`` for the highest-scoring candidate
    at or above ``threshold``, or ``(None, 0.0)`` when none qualifies. An exact
    string match short-circuits to confidence 1.0. ``candidate_fps`` is any
    iterable of fingerprint strings."""
    best_fp = None
    best_score = 0.0
    for cand in candidate_fps:
        score = fuzzy_similarity(target_fp, cand)
        if score > best_score:
            best_fp, best_score = cand, score
            if best_score == 1.0:
                break
    if best_fp is not None and best_score >= threshold:
        return best_fp, best_score
    return None, 0.0
