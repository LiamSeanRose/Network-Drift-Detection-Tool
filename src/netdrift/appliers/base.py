"""appliers/base.py — the applier plugin contract and in-tree registry.

An applier is a callable ``apply(remediation, drift, device, *, dry_run) ->
ApplyResult`` that executes (or dry-runs) a known-issue fix on a device.
Mirrors the collector plugin contract in ``collectors/base.py``.

What lives here:
    ApplyResult              — NamedTuple returned by every applier.
    Applier                  — typing.Protocol describing the callable shape.
    RemediationBlockedError  — raised when drift hits the do-not-auto-apply list.
    DuplicatePlatformError   — raised when two appliers claim one platform.
    check_blocked()          — enforces the hard do-not-auto-apply list.
    register(...)            — decorator each applier applies to its apply fn.
    registered()             — read the populated registry (used by registry.py).

Discovery (importing applier modules so their decorators run) and the public
``get_applier`` / ``build_appliers`` helpers live in ``registry.py``; this
module only owns the contract and the store.
"""

from typing import Callable, NamedTuple, Protocol, runtime_checkable


CONTRACT_VERSION = 1


class ApplyResult(NamedTuple):
    """Result returned by every applier call."""

    transport: str           # "cli" | "gnmi"
    rendered_commands: str   # exact commands / gNMI updates that were or would be sent
    dry_run_diff: str        # candidate diff from live dry-run; "" if not available
    applied: bool            # True if committed to device; False when dry_run=True


@runtime_checkable
class Applier(Protocol):
    """Structural type for an applier callable.

    Takes the remediation payload, the live drift record, and the device dict,
    and either commits a fix (dry_run=False) or returns a diff without committing
    (dry_run=True). Plain functions satisfy this — no inheritance needed.
    """

    def __call__(
        self,
        remediation: dict,
        drift: dict,
        device: dict,
        *,
        dry_run: bool = False,
    ) -> ApplyResult: ...


class RemediationBlockedError(Exception):
    """The drift record matches the hard do-not-auto-apply list.

    Raised by check_blocked() before any vendor code runs. Catching this at the
    orchestration layer is the expected pattern — it is not a crash, it is a
    deliberate refusal to proceed.
    """


class DuplicatePlatformError(Exception):
    """Two appliers registered the same platform string.

    Loud by design — same stance as the collector registry. A silent last-writer-
    wins would route a live device to the wrong applier.
    """


class RegisteredApplier(NamedTuple):
    """One registry entry: the applier callable plus its registration metadata."""

    fn: Callable
    platform: str


# The registry. Populated as a side effect of importing each applier module
# (the @register decorator runs at import time). registry.py owns the importing.
_REGISTRY: dict[str, RegisteredApplier] = {}


# Fields that are operational symptoms, not directly configurable.
# check_blocked() refuses to proceed when drift targets these.
_BLOCKED_FIELDS = frozenset({"session_state", "adjacency_state"})


def check_blocked(drift: dict, device: dict) -> None:  # noqa: ARG001 — device reserved for mgmt-iface check
    """Raise RemediationBlockedError if the drift matches the hard do-not-auto-apply list.

    Enforced here for all remediation kinds before any vendor code runs.
    Per-vendor appliers add their own management-interface checks on top.

    Blocked cases:
      - Operational-symptom fields (session_state, adjacency_state).
      - missing_in_intent with null/empty intent — "undocumented" ≠ authorization
        to delete.
    """
    field = drift.get("field", "")
    drift_kind = drift.get("drift_kind", "")
    intent = drift.get("intent")

    if field in _BLOCKED_FIELDS:
        raise RemediationBlockedError(
            f"Field '{field}' is an operational symptom and is not directly "
            "configurable via remediation."
        )

    if drift_kind == "missing_in_intent" and intent in (None, "", [], {}):
        raise RemediationBlockedError(
            "drift_kind 'missing_in_intent' with null or empty intent is not "
            "authorization to delete the object from the device."
        )


def register(platform: str):
    """Decorator: register an applier's apply function under a platform string.

    Args:
        platform: the canonical platform string (schema.md Section 4), e.g.
            "arista_eos". This is the key registry.get_applier() dispatches on.

    Returns the function unchanged, so appliers stay plain callables.

    Raises:
        DuplicatePlatformError: if `platform` is already registered.
    """

    def decorator(fn: Callable) -> Callable:
        existing = _REGISTRY.get(platform)
        if existing is not None:
            raise DuplicatePlatformError(
                f"Platform '{platform}' already registered by "
                f"{existing.fn.__module__}.{existing.fn.__qualname__}; "
                f"{fn.__module__}.{fn.__qualname__} cannot claim it too."
            )
        _REGISTRY[platform] = RegisteredApplier(fn=fn, platform=platform)
        return fn

    return decorator


def registered() -> dict[str, RegisteredApplier]:
    """Return a copy of the current registry (platform -> RegisteredApplier)."""
    return dict(_REGISTRY)


def _reset_registry() -> None:
    """Clear the registry. Test-only — lets a test rebuild from a clean slate."""
    _REGISTRY.clear()
