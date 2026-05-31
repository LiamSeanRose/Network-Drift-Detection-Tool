"""collectors/base.py — the collector plugin contract and in-tree registry.

A collector is a callable ``get_reality(device: dict) -> dict`` that returns the
device's real state in the normalized schema (docs/schema.md). Until v1.0 the
three collectors were wired into ``pipeline.py`` and ``cli.py`` by hand; this
module replaces that with a single registry a collector opts into via the
``@register`` decorator, so adding a vendor no longer edits core dispatch code.

What lives here:
    Collector            — a typing.Protocol describing the callable shape.
    register(...)        — decorator each collector applies to its get_reality.
    registered()         — read the populated registry (used by registry.py).
    DuplicatePlatformError — raised when two collectors claim one platform.

Discovery (importing the collector modules so their decorators run) and the
public ``build_collectors`` / ``build_platform_map`` helpers live in
``registry.py``; this module only owns the contract and the store.
"""

from typing import Callable, NamedTuple, Protocol, runtime_checkable


CONTRACT_VERSION = 1


@runtime_checkable
class Collector(Protocol):
    """Structural type for a collector callable.

    A collector takes the device dict (name + connection details) and returns
    its reality as a normalized schema dict. This is a type-check aid only —
    plain functions satisfy it, nothing is enforced at runtime.
    """

    def __call__(self, device: dict) -> dict: ...


class DuplicatePlatformError(Exception):
    """Two collectors registered the same platform string.

    Loud by design — the same "fail clearly, don't guess" stance netbox_client
    takes on an unknown platform. A silent last-writer-wins would route a real
    device's credentials to the wrong collector.
    """


class RegisteredCollector(NamedTuple):
    """One registry entry: the collector callable plus its registration metadata."""

    fn: Callable[[dict], dict]
    platform: str
    netbox_slugs: tuple[str, ...]


# The registry. Populated as a side effect of importing each collector module
# (the @register decorator runs at import time). registry.py owns the importing.
_REGISTRY: dict[str, RegisteredCollector] = {}


def register(platform: str, *, netbox_slugs: tuple[str, ...] = ()):
    """Decorator: register a collector's get_reality under a platform string.

    Args:
        platform: the canonical platform string (schema.md Section 4), e.g.
            "arista_eos". This is the key pipeline/cli dispatch on.
        netbox_slugs: the NetBox/Nautobot platform slugs that map to this
            platform (e.g. "arista-eos", "eos"). These are sensible defaults;
            an operator with nonstandard slugs overrides them on the intent side.

    Returns the function unchanged, so collectors stay plain callables.

    Raises:
        DuplicatePlatformError: if `platform` is already registered.
    """

    def decorator(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        existing = _REGISTRY.get(platform)
        if existing is not None:
            raise DuplicatePlatformError(
                f"Platform '{platform}' already registered by "
                f"{existing.fn.__module__}.{existing.fn.__qualname__}; "
                f"{fn.__module__}.{fn.__qualname__} cannot claim it too."
            )
        _REGISTRY[platform] = RegisteredCollector(
            fn=fn, platform=platform, netbox_slugs=tuple(netbox_slugs)
        )
        return fn

    return decorator


def registered() -> dict[str, RegisteredCollector]:
    """Return a copy of the current registry (platform -> RegisteredCollector)."""
    return dict(_REGISTRY)


def _reset_registry() -> None:
    """Clear the registry. Test-only — lets a test rebuild from a clean slate."""
    _REGISTRY.clear()
