"""tests/test_applier_registry.py — applier registry + plugin contract.

Mirrors test_registry.py for the collector side. Verifies:
- @register / DuplicatePlatformError
- get_applier raises on unknown platform
- build_appliers returns the full map
- broken applier module is skipped, not fatal
- Applier Protocol isinstance check
- check_blocked enforces the hard do-not-auto-apply list
"""

import logging

import pytest

from netdrift.appliers import base, registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Isolate each test — reset applier registry before and after."""
    base._reset_registry()
    registry._reset()
    yield
    base._reset_registry()
    registry._reset()


# ---------------------------------------------------------------------------
# Registry mechanics
# ---------------------------------------------------------------------------


def _make_fake_applier(name="fake"):
    def apply(remediation, drift, device, *, dry_run=False):
        return base.ApplyResult(
            transport="cli", rendered_commands="", dry_run_diff="", applied=False
        )
    apply.__name__ = name
    apply.__qualname__ = name
    return apply


def test_build_appliers_returns_registered_platforms():
    fn = _make_fake_applier()
    base.register("test_eos")(fn)
    appliers = registry.build_appliers()
    assert "test_eos" in appliers
    assert appliers["test_eos"] is fn


def test_get_applier_returns_callable():
    fn = _make_fake_applier()
    base.register("test_eos")(fn)
    assert registry.get_applier("test_eos") is fn


def test_get_applier_raises_on_unknown_platform():
    with pytest.raises(KeyError, match="no_such_platform"):
        registry.get_applier("no_such_platform")


def test_duplicate_platform_registration_raises():
    base.register("test_eos")(_make_fake_applier("a"))
    with pytest.raises(base.DuplicatePlatformError, match="test_eos"):
        base.register("test_eos")(_make_fake_applier("b"))


def test_registered_applier_satisfies_the_applier_protocol():
    fn = _make_fake_applier()
    base.register("test_eos")(fn)
    assert isinstance(fn, base.Applier)


def test_broken_applier_module_is_skipped(monkeypatch, caplog):
    monkeypatch.setattr(registry, "APPLIER_MODULES", ("nonexistent_vendor",))
    registry._reset()
    with caplog.at_level(logging.WARNING):
        appliers = registry.build_appliers()
    assert "nonexistent_vendor" not in str(appliers)
    assert any("nonexistent_vendor" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# check_blocked — hard do-not-auto-apply enforcement
# ---------------------------------------------------------------------------

_DEVICE = {"name": "core-sw-01"}


def test_check_blocked_raises_on_session_state():
    drift = {"field": "session_state", "drift_kind": "value_mismatch", "intent": "established"}
    with pytest.raises(base.RemediationBlockedError, match="operational symptom"):
        base.check_blocked(drift, _DEVICE)


def test_check_blocked_raises_on_adjacency_state():
    drift = {"field": "adjacency_state", "drift_kind": "value_mismatch", "intent": "full"}
    with pytest.raises(base.RemediationBlockedError, match="operational symptom"):
        base.check_blocked(drift, _DEVICE)


def test_check_blocked_raises_on_missing_in_intent_with_empty_string_intent():
    drift = {"field": "description", "drift_kind": "missing_in_intent", "intent": ""}
    with pytest.raises(base.RemediationBlockedError, match="authorization to delete"):
        base.check_blocked(drift, _DEVICE)


def test_check_blocked_raises_on_missing_in_intent_with_none_intent():
    drift = {"field": "description", "drift_kind": "missing_in_intent", "intent": None}
    with pytest.raises(base.RemediationBlockedError, match="authorization to delete"):
        base.check_blocked(drift, _DEVICE)


def test_check_blocked_raises_on_missing_in_intent_with_empty_list_intent():
    drift = {"field": "tagged_vlans", "drift_kind": "missing_in_intent", "intent": []}
    with pytest.raises(base.RemediationBlockedError, match="authorization to delete"):
        base.check_blocked(drift, _DEVICE)


def test_check_blocked_raises_on_missing_in_intent_with_empty_dict_intent():
    drift = {"field": "description", "drift_kind": "missing_in_intent", "intent": {}}
    with pytest.raises(base.RemediationBlockedError, match="authorization to delete"):
        base.check_blocked(drift, _DEVICE)


def test_check_blocked_allows_normal_value_mismatch():
    drift = {"field": "description", "drift_kind": "value_mismatch", "intent": "Uplink"}
    base.check_blocked(drift, _DEVICE)  # must not raise


def test_check_blocked_allows_missing_in_reality_with_intent():
    drift = {"field": "enabled", "drift_kind": "missing_in_reality", "intent": True}
    base.check_blocked(drift, _DEVICE)  # must not raise


def test_check_blocked_allows_missing_in_intent_with_real_intent():
    drift = {"field": "description", "drift_kind": "missing_in_intent", "intent": "Uplink"}
    base.check_blocked(drift, _DEVICE)  # must not raise
