"""tests/test_nokia_applier.py — Nokia SR Linux applier unit tests.

Covers the gNMI path rendering, diff synthesis, and the full apply() flow
without opening a socket. FakeGNMIApplierClient stands in for pygnmi's
gNMIclient — it records Set calls and returns canned Get values keyed by path.
"""

import contextlib
import json

import pytest

from netdrift.appliers.nokia import (
    _apply_via_gnmi,
    _block_mgmt_interface,
    _render_restore_intent,
    _synthesize_diff,
    apply,
)
from netdrift.appliers.base import ApplyResult, RemediationBlockedError


# ---------------------------------------------------------------------------
# Fake gNMI client
# ---------------------------------------------------------------------------

class FakeGNMIApplierClient:
    """Stand-in for pygnmi's gNMIclient.

    current_values maps a path string to the value Get should return for it.
    Paths not present return an empty notification (simulating 'not set').
    set() calls are recorded in set_calls for assertion.
    """

    def __init__(self, current_values=None):
        self._current = current_values or {}
        self.set_calls: list = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, path=None, datatype=None):
        key = path[0] if path else ""
        val = self._current.get(key)
        if val is None:
            return {"notification": []}
        return {"notification": [{"update": [{"val": val}]}]}

    def set(self, update=None, replace=None, delete=None):
        if update:
            self.set_calls.append(list(update))


def _fake_conn(fake_client):
    """Return a _gnmi_conn-compatible context manager that yields fake_client."""
    @contextlib.contextmanager
    def _conn(device):
        yield fake_client
    return _conn


DEVICE = {
    "name": "nokia-sw-01",
    "hostname": "172.20.20.21",
    "username": "admin",
    "password": "NokiaSrl1!",
}


# ---------------------------------------------------------------------------
# _render_restore_intent
# ---------------------------------------------------------------------------

def test_render_interface_description():
    drift = {"object": "interface:ethernet-1/1", "field": "description", "intent": "Uplink"}
    updates = _render_restore_intent(drift)
    assert updates == [("/interface[name=ethernet-1/1]/description", "Uplink")]


def test_render_interface_description_empty_intent():
    drift = {"object": "interface:ethernet-1/1", "field": "description", "intent": ""}
    updates = _render_restore_intent(drift)
    assert updates == [("/interface[name=ethernet-1/1]/description", "")]


def test_render_interface_description_none_intent():
    drift = {"object": "interface:ethernet-1/1", "field": "description", "intent": None}
    updates = _render_restore_intent(drift)
    assert updates == [("/interface[name=ethernet-1/1]/description", "")]


def test_render_interface_enabled_true():
    drift = {"object": "interface:ethernet-1/2", "field": "enabled", "intent": True}
    updates = _render_restore_intent(drift)
    assert updates == [("/interface[name=ethernet-1/2]/admin-state", "enable")]


def test_render_interface_enabled_false():
    drift = {"object": "interface:ethernet-1/2", "field": "enabled", "intent": False}
    updates = _render_restore_intent(drift)
    assert updates == [("/interface[name=ethernet-1/2]/admin-state", "disable")]


def test_render_interface_unsupported_field_raises():
    drift = {"object": "interface:ethernet-1/1", "field": "ip_addresses", "intent": ["10.0.0.1/30"]}
    with pytest.raises(NotImplementedError, match="ip_addresses"):
        _render_restore_intent(drift)


def test_render_interface_mode_raises():
    drift = {"object": "interface:ethernet-1/1", "field": "mode", "intent": "access"}
    with pytest.raises(NotImplementedError, match="mode"):
        _render_restore_intent(drift)


def test_render_vlan_raises():
    drift = {"object": "vlan:10", "field": "name", "intent": "users"}
    with pytest.raises(NotImplementedError, match="vlan"):
        _render_restore_intent(drift)


def test_render_bgp_neighbor_raises():
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "description", "intent": "peer"}
    with pytest.raises(NotImplementedError, match="bgp_neighbor"):
        _render_restore_intent(drift)


# ---------------------------------------------------------------------------
# _block_mgmt_interface
# ---------------------------------------------------------------------------

def test_block_mgmt_interface_raises_for_mgmt0():
    drift = {"object": "interface:mgmt0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="mgmt0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_raises_for_mgmt0_subinterface():
    drift = {"object": "interface:mgmt0.0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="mgmt0.0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_allows_ethernet():
    drift = {"object": "interface:ethernet-1/1", "field": "description", "intent": "uplink"}
    _block_mgmt_interface(drift)  # must not raise


# ---------------------------------------------------------------------------
# _synthesize_diff
# ---------------------------------------------------------------------------

def test_synthesize_diff_shows_change():
    fake = FakeGNMIApplierClient(
        current_values={"/interface[name=ethernet-1/1]/description": "old desc"}
    )
    diff = _synthesize_diff(fake, [("/interface[name=ethernet-1/1]/description", "new desc")])
    assert "--- /interface[name=ethernet-1/1]/description: 'old desc'" in diff
    assert "+++ /interface[name=ethernet-1/1]/description: 'new desc'" in diff


def test_synthesize_diff_empty_when_already_correct():
    fake = FakeGNMIApplierClient(
        current_values={"/interface[name=ethernet-1/1]/description": "Uplink"}
    )
    diff = _synthesize_diff(fake, [("/interface[name=ethernet-1/1]/description", "Uplink")])
    assert diff == ""


def test_synthesize_diff_treats_missing_as_none():
    # Path not in current_values — get() returns empty notification → None.
    fake = FakeGNMIApplierClient(current_values={})
    diff = _synthesize_diff(fake, [("/interface[name=ethernet-1/1]/description", "Uplink")])
    assert "None" in diff
    assert "Uplink" in diff


def test_synthesize_diff_multiple_paths():
    fake = FakeGNMIApplierClient(current_values={
        "/interface[name=ethernet-1/1]/description": "old",
        "/interface[name=ethernet-1/2]/admin-state": "enable",  # already correct
    })
    updates = [
        ("/interface[name=ethernet-1/1]/description", "new"),
        ("/interface[name=ethernet-1/2]/admin-state", "enable"),
    ]
    diff = _synthesize_diff(fake, updates)
    assert "ethernet-1/1" in diff
    assert "ethernet-1/2" not in diff  # already correct, omitted


# ---------------------------------------------------------------------------
# _apply_via_gnmi — dry_run=True
# ---------------------------------------------------------------------------

def test_apply_via_gnmi_dry_run_does_not_call_set():
    fake = FakeGNMIApplierClient(
        current_values={"/interface[name=ethernet-1/1]/description": "old"}
    )
    result = _apply_via_gnmi(fake, [("/interface[name=ethernet-1/1]/description", "new")], dry_run=True)
    assert result.applied is False
    assert result.transport == "gnmi"
    assert fake.set_calls == []


def test_apply_via_gnmi_dry_run_returns_diff():
    fake = FakeGNMIApplierClient(
        current_values={"/interface[name=ethernet-1/1]/description": "old"}
    )
    result = _apply_via_gnmi(fake, [("/interface[name=ethernet-1/1]/description", "new")], dry_run=True)
    assert "old" in result.dry_run_diff
    assert "new" in result.dry_run_diff


# ---------------------------------------------------------------------------
# _apply_via_gnmi — dry_run=False (commit)
# ---------------------------------------------------------------------------

def test_apply_via_gnmi_commit_calls_set():
    fake = FakeGNMIApplierClient()
    updates = [("/interface[name=ethernet-1/1]/description", "Uplink")]
    result = _apply_via_gnmi(fake, updates, dry_run=False)
    assert result.applied is True
    assert fake.set_calls == [updates]


def test_apply_via_gnmi_rendered_commands_is_valid_json():
    fake = FakeGNMIApplierClient()
    updates = [("/interface[name=ethernet-1/1]/description", "Uplink")]
    result = _apply_via_gnmi(fake, updates, dry_run=False)
    parsed = json.loads(result.rendered_commands)
    assert parsed == [{"path": "/interface[name=ethernet-1/1]/description", "val": "Uplink"}]


def test_apply_via_gnmi_transport_is_gnmi():
    fake = FakeGNMIApplierClient()
    result = _apply_via_gnmi(fake, [("/some/path", "val")], dry_run=False)
    assert result.transport == "gnmi"


# ---------------------------------------------------------------------------
# apply — restore_intent, dry_run=True
# ---------------------------------------------------------------------------

def test_apply_restore_intent_dry_run(monkeypatch):
    fake = FakeGNMIApplierClient(
        current_values={"/interface[name=ethernet-1/1]/description": "old desc"}
    )
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(fake))

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:ethernet-1/1", "field": "description",
             "intent": "Uplink", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert isinstance(result, ApplyResult)
    assert result.applied is False
    assert result.transport == "gnmi"
    assert "ethernet-1/1" in result.rendered_commands
    assert fake.set_calls == []


# ---------------------------------------------------------------------------
# apply — restore_intent, dry_run=False (commit)
# ---------------------------------------------------------------------------

def test_apply_restore_intent_commit(monkeypatch):
    fake = FakeGNMIApplierClient()
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(fake))

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "enabled",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:ethernet-1/1", "field": "enabled",
             "intent": True, "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=False)

    assert result.applied is True
    assert len(fake.set_calls) == 1
    assert fake.set_calls[0] == [("/interface[name=ethernet-1/1]/admin-state", "enable")]


# ---------------------------------------------------------------------------
# apply — raw_snippet
# ---------------------------------------------------------------------------

def test_apply_raw_snippet_dry_run(monkeypatch):
    fake = FakeGNMIApplierClient(
        current_values={"/interface[name=ethernet-1/1]/description": "old"}
    )
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(fake))

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {
            "nokia_srlinux": {
                "transport": "gnmi",
                "updates": [{"path": "/interface[name=ethernet-1/1]/description", "val": "new"}],
            }
        },
    }
    drift = {"object": "interface:ethernet-1/1", "field": "description",
             "intent": "new", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert result.applied is False
    assert result.transport == "gnmi"
    parsed = json.loads(result.rendered_commands)
    assert parsed[0]["path"] == "/interface[name=ethernet-1/1]/description"
    assert fake.set_calls == []


def test_apply_raw_snippet_commit(monkeypatch):
    fake = FakeGNMIApplierClient()
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(fake))

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {
            "nokia_srlinux": {
                "transport": "gnmi",
                "updates": [{"path": "/interface[name=ethernet-1/1]/description", "val": "new"}],
            }
        },
    }
    drift = {"object": "interface:ethernet-1/1", "field": "description",
             "intent": "new", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=False)

    assert result.applied is True
    assert len(fake.set_calls) == 1


def test_apply_raw_snippet_missing_platform_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(FakeGNMIApplierClient()))

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {"arista_eos": {"transport": "cli", "body": "!"}},
    }
    drift = {"object": "interface:ethernet-1/1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}

    with pytest.raises(ValueError, match="nokia_srlinux"):
        apply(remediation, drift, DEVICE)


# ---------------------------------------------------------------------------
# apply — null kind and unknown kind
# ---------------------------------------------------------------------------

def test_apply_null_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(FakeGNMIApplierClient()))
    remediation = {"kind": None}
    drift = {"object": "interface:ethernet-1/1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="null"):
        apply(remediation, drift, DEVICE)


def test_apply_unknown_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(FakeGNMIApplierClient()))
    remediation = {"kind": "magic"}
    drift = {"object": "interface:ethernet-1/1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="magic"):
        apply(remediation, drift, DEVICE)


# ---------------------------------------------------------------------------
# apply — blocked cases
# ---------------------------------------------------------------------------

def test_apply_blocked_session_state(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(FakeGNMIApplierClient()))
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "bgp_neighbor", "field": "session_state",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "session_state",
             "intent": "established", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_mgmt0(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.nokia._gnmi_conn", _fake_conn(FakeGNMIApplierClient()))
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:mgmt0", "field": "description",
             "intent": "oob-mgmt", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError, match="mgmt0"):
        apply(remediation, drift, DEVICE)
