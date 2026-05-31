"""tests/test_arista_applier.py — Arista EOS applier unit tests.

Covers the transform and NAPALM merge-candidate flow without opening a socket.
FakeNapalmApplierConn stands in for the NAPALM EOS driver and records which
methods were called so tests can assert on dry_run vs commit behaviour.
"""

import pytest

from netdrift.appliers.arista import (
    _block_mgmt_interface,
    _render_restore_intent,
    apply,
)
from netdrift.appliers.base import ApplyResult, RemediationBlockedError


# ---------------------------------------------------------------------------
# Fake NAPALM connection
# ---------------------------------------------------------------------------

FAKE_DIFF = "--- running\n+++ candidate\n@@ -1 +1 @@\n-description old\n+description new"


class FakeNapalmApplierConn:
    """Fake NAPALM EOS connection. No socket opened; call tracking only."""

    def __init__(self):
        self.loaded_config = None
        self.committed = False
        self.discarded = False

    def open(self):
        pass

    def close(self):
        pass

    def load_merge_candidate(self, config=None):
        self.loaded_config = config

    def compare_config(self):
        return FAKE_DIFF

    def commit_config(self):
        self.committed = True

    def discard_config(self):
        self.discarded = True


DEVICE = {
    "name": "core-sw-01",
    "hostname": "172.20.20.11",
    "username": "admin",
    "password": "admin",
}


# ---------------------------------------------------------------------------
# _render_restore_intent — supported fields
# ---------------------------------------------------------------------------

def test_render_interface_description():
    drift = {"object": "interface:Ethernet1", "field": "description", "intent": "Uplink to core"}
    assert _render_restore_intent(drift) == "interface Ethernet1\n   description Uplink to core"


def test_render_interface_description_empty_string():
    drift = {"object": "interface:Ethernet1", "field": "description", "intent": ""}
    assert _render_restore_intent(drift) == "interface Ethernet1\n   description "


def test_render_interface_enabled_true():
    drift = {"object": "interface:Ethernet2", "field": "enabled", "intent": True}
    assert _render_restore_intent(drift) == "interface Ethernet2\n   no shutdown"


def test_render_interface_enabled_false():
    drift = {"object": "interface:Ethernet2", "field": "enabled", "intent": False}
    assert _render_restore_intent(drift) == "interface Ethernet2\n   shutdown"


def test_render_interface_untagged_vlan():
    drift = {"object": "interface:Ethernet2", "field": "untagged_vlan", "intent": 10}
    assert _render_restore_intent(drift) == "interface Ethernet2\n   switchport access vlan 10"


def test_render_interface_tagged_vlans():
    drift = {"object": "interface:Ethernet3", "field": "tagged_vlans", "intent": [10, 20, 30]}
    assert _render_restore_intent(drift) == "interface Ethernet3\n   switchport trunk allowed vlan 10,20,30"


def test_render_interface_tagged_vlans_empty():
    drift = {"object": "interface:Ethernet3", "field": "tagged_vlans", "intent": []}
    assert _render_restore_intent(drift) == "interface Ethernet3\n   switchport trunk allowed vlan "


def test_render_vlan_name():
    drift = {"object": "vlan:20", "field": "name", "intent": "voice"}
    assert _render_restore_intent(drift) == "vlan 20\n   name voice"


# ---------------------------------------------------------------------------
# _render_restore_intent — unsupported fields raise NotImplementedError
# ---------------------------------------------------------------------------

def test_render_interface_ip_addresses_raises():
    drift = {"object": "interface:Ethernet1", "field": "ip_addresses", "intent": ["10.0.0.1/30"]}
    with pytest.raises(NotImplementedError, match="ip_addresses"):
        _render_restore_intent(drift)


def test_render_interface_mode_raises():
    drift = {"object": "interface:Ethernet1", "field": "mode", "intent": "access"}
    with pytest.raises(NotImplementedError, match="mode"):
        _render_restore_intent(drift)


def test_render_bgp_neighbor_raises():
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "description", "intent": "peer"}
    with pytest.raises(NotImplementedError, match="bgp_neighbor"):
        _render_restore_intent(drift)


# ---------------------------------------------------------------------------
# _block_mgmt_interface
# ---------------------------------------------------------------------------

def test_block_mgmt_interface_raises_for_management0():
    drift = {"object": "interface:Management0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="Management0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_raises_for_management1():
    drift = {"object": "interface:Management1", "field": "enabled", "intent": True}
    with pytest.raises(RemediationBlockedError, match="Management1"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_allows_ethernet():
    drift = {"object": "interface:Ethernet1", "field": "description", "intent": "Uplink"}
    _block_mgmt_interface(drift)  # must not raise


# ---------------------------------------------------------------------------
# apply — restore_intent, dry_run=True
# ---------------------------------------------------------------------------

def test_apply_restore_intent_dry_run(monkeypatch):
    fake = FakeNapalmApplierConn()
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: fake)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:Ethernet1", "field": "description",
             "intent": "Uplink", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert isinstance(result, ApplyResult)
    assert result.applied is False
    assert result.transport == "cli"
    assert "Ethernet1" in result.rendered_commands
    assert result.dry_run_diff == FAKE_DIFF
    assert fake.discarded is True
    assert fake.committed is False


# ---------------------------------------------------------------------------
# apply — restore_intent, dry_run=False (commit)
# ---------------------------------------------------------------------------

def test_apply_restore_intent_commit(monkeypatch):
    fake = FakeNapalmApplierConn()
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: fake)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "enabled",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:Ethernet1", "field": "enabled",
             "intent": True, "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=False)

    assert result.applied is True
    assert fake.committed is True
    assert fake.discarded is False


# ---------------------------------------------------------------------------
# apply — raw_snippet
# ---------------------------------------------------------------------------

def test_apply_raw_snippet_dry_run(monkeypatch):
    fake = FakeNapalmApplierConn()
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: fake)

    snippet = "interface Ethernet1\n   description Fixed"
    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {"arista_eos": {"transport": "cli", "body": snippet}},
    }
    drift = {"object": "interface:Ethernet1", "field": "description",
             "intent": "Fixed", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert result.applied is False
    assert result.rendered_commands == snippet
    assert fake.loaded_config == snippet


def test_apply_raw_snippet_missing_platform_raises(monkeypatch):
    fake = FakeNapalmApplierConn()
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: fake)

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {"cisco_iosxe": {"transport": "cli", "body": "!"}},
    }
    drift = {"object": "interface:Ethernet1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}

    with pytest.raises(ValueError, match="arista_eos"):
        apply(remediation, drift, DEVICE)


# ---------------------------------------------------------------------------
# apply — null kind and unknown kind
# ---------------------------------------------------------------------------

def test_apply_null_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: FakeNapalmApplierConn())
    remediation = {"kind": None}
    drift = {"object": "interface:Ethernet1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="null"):
        apply(remediation, drift, DEVICE)


def test_apply_unknown_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: FakeNapalmApplierConn())
    remediation = {"kind": "magic"}
    drift = {"object": "interface:Ethernet1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="magic"):
        apply(remediation, drift, DEVICE)


# ---------------------------------------------------------------------------
# apply — blocked cases (check_blocked + mgmt interface)
# ---------------------------------------------------------------------------

def test_apply_blocked_session_state(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: FakeNapalmApplierConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "bgp_neighbor", "field": "session_state",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "session_state",
             "intent": "established", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_management_interface(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.arista._napalm_conn", lambda device: FakeNapalmApplierConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:Management0", "field": "description",
             "intent": "mgmt", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError, match="Management0"):
        apply(remediation, drift, DEVICE)
