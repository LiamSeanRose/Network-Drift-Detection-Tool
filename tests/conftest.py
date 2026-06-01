"""Shared pytest configuration.

Registers the ``unvalidated_fixture`` marker and auto-applies it to the Nokia
SR Linux BGP/OSPF collector tests.

Those tests pass against ``FakeGNMIClient`` payloads whose shapes were modelled
from the SR Linux YANG spec, not captured from a live routing node (see
CLAUDE.md, "Open design questions"). They give real parser coverage today, but
the fixture shapes must be re-verified against the device once routing is
actually configured on the Nokia — SR Linux gNMI path/JSON encoding has
historically differed from the spec examples, and a mismatch would let the
collector silently return empty or wrong routing state in production while these
unit tests stay green.

The marker makes that surface discoverable rather than silent:
    pytest -m unvalidated_fixture          # list exactly what to re-verify
    pytest -m "not unvalidated_fixture"    # exclude the provisional surface

When the fixtures are validated against a live Nokia, drop the matching prefixes
below (and this note).
"""

import pytest

# Test-name prefixes in tests/test_nokia.py that depend on unverified SR Linux
# gNMI routing fixture shapes.
_NOKIA_ROUTING_PREFIXES = (
    "test_build_bgp_neighbors",
    "test_build_ospf_adjacencies",
    "test_get_reality_builds_bgp_neighbors",
    "test_get_reality_builds_ospf",
)


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "unvalidated_fixture: relies on a fixture shape not yet verified against "
        "a live device (Nokia SR Linux BGP/OSPF gNMI — see CLAUDE.md).",
    )


def pytest_collection_modifyitems(items):
    for item in items:
        if "test_nokia.py" in item.nodeid and item.name.startswith(_NOKIA_ROUTING_PREFIXES):
            item.add_marker(pytest.mark.unvalidated_fixture)
