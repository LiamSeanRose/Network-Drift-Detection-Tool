"""collectors/registry.py — single source of truth for collector dispatch.

Replaces the two hand-maintained ``COLLECTORS`` dicts (one in pipeline.py, a
duplicate in cli.py) and the hardcoded ``PLATFORM_MAP`` in netbox_client.py.
Importing each collector module runs its ``@register`` decorator (see base.py),
populating the registry; this module exposes that registry as two plain dicts:

    build_collectors()    -> {platform: get_reality}   (pipeline/cli dispatch)
    build_platform_map()  -> {netbox_slug: platform}   (intent-side slug mapping)

Loading is lazy and fault-isolated: each collector module is imported inside its
own try/except, so one broken collector (a syntax error, a missing optional
dependency) logs a warning and is skipped rather than taking down the whole
tool. The same isolation pattern will carry over when out-of-tree entry-point
plugins are added (currently deferred — see docs/PROJECT_PLAN.md §15).
"""

import importlib
import logging

from netdrift.collectors import COLLECTOR_MODULES, base

logger = logging.getLogger(__name__)

# Guards the one-time import of the collector modules. The registry itself lives
# in base._REGISTRY; this flag just avoids re-walking COLLECTOR_MODULES on every
# build call (re-imports are no-ops, but the flag keeps it cheap and explicit).
_loaded = False


def _ensure_loaded() -> None:
    """Import each collector module once so its @register decorator runs.

    Per-module fault isolation: a module that fails to import is logged and
    skipped; the others still load.
    """
    global _loaded
    if _loaded:
        return
    for name in COLLECTOR_MODULES:
        try:
            importlib.import_module(f"netdrift.collectors.{name}")
        except Exception as exc:  # noqa: BLE001 — a bad plugin must not crash dispatch
            logger.warning("Skipping collector '%s': %s", name, exc)
    _loaded = True


def build_collectors() -> dict:
    """Return the platform -> get_reality dispatch map.

    This is the shape pipeline.run_drift_check(collectors=...) and the cli
    already expect, so they can default to it without changing the fake-injection
    seam tests rely on.
    """
    _ensure_loaded()
    return {platform: rc.fn for platform, rc in base.registered().items()}


def build_platform_map() -> dict:
    """Return the NetBox/Nautobot slug -> canonical platform map.

    Built from each collector's registered ``netbox_slugs``. A slug claimed by
    two collectors is a registration bug; we log it and let the last one win
    rather than crash (the duplicate *platform* case is already a hard error in
    base.register).
    """
    _ensure_loaded()
    slug_map: dict[str, str] = {}
    for platform, rc in base.registered().items():
        for slug in rc.netbox_slugs:
            if slug in slug_map and slug_map[slug] != platform:
                logger.warning(
                    "NetBox slug '%s' claimed by both '%s' and '%s'; using '%s'.",
                    slug, slug_map[slug], platform, platform,
                )
            slug_map[slug] = platform
    return slug_map


def _reset() -> None:
    """Force the next build to re-import the collector modules. Test-only."""
    global _loaded
    _loaded = False
