"""appliers/registry.py — single source of truth for applier dispatch.

Mirrors ``collectors/registry.py``. Importing each applier module runs its
``@register`` decorator (see base.py), populating the registry; this module
exposes two public helpers:

    get_applier(platform)  -> the applier callable for that platform
    build_appliers()       -> {platform: apply_fn}  (full map; mainly for tests)

Loading is lazy and fault-isolated: each applier module is imported inside its
own try/except, so one broken applier logs a warning and is skipped rather than
taking down the whole tool.
"""

import importlib
import logging

from netdrift.appliers import APPLIER_MODULES, base

logger = logging.getLogger(__name__)

_loaded = False


def _ensure_loaded() -> None:
    """Import each applier module once so its @register decorator runs."""
    global _loaded
    if _loaded:
        return
    for name in APPLIER_MODULES:
        try:
            importlib.import_module(f"netdrift.appliers.{name}")
        except Exception as exc:  # noqa: BLE001 — a bad plugin must not crash dispatch
            logger.warning("Skipping applier '%s': %s", name, exc)
    _loaded = True


def get_applier(platform: str):
    """Return the applier callable for the given platform.

    This is the primary dispatch entry point for the orchestration layer.

    Raises:
        KeyError: if no applier is registered for `platform`.
    """
    _ensure_loaded()
    try:
        return base.registered()[platform].fn
    except KeyError:
        raise KeyError(
            f"No applier registered for platform '{platform}'. "
            f"Registered: {sorted(base.registered())}"
        )


def build_appliers() -> dict:
    """Return the full platform -> apply_fn dispatch map.

    Convenience for tests and for any tooling that needs to enumerate all
    registered appliers. Prefer get_applier() for normal dispatch.
    """
    _ensure_loaded()
    return {platform: ra.fn for platform, ra in base.registered().items()}


def _reset() -> None:
    """Force the next build to re-import the applier modules. Test-only."""
    global _loaded
    _loaded = False
