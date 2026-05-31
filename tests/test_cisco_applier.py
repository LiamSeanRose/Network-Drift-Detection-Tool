"""tests/test_cisco_applier.py — Cisco IOS-XE applier unit tests.

Covers the transform and NAPALM merge-candidate flow without opening a socket.
FakeNapalmCiscoConn stands in for the NAPALM IOS driver and records which
methods were called so tests can assert on dry_run vs commit behaviour,
including the post-commit verification path that is unique to the Cisco applier.
"""

import logging

import pytest

from netdrift.appliers.cisco import (
    _block_mgmt_interface,
    _render_restore_intent,
    apply,
)
from netdrift.appliers.base import ApplyResult, RemediationBlockedError


# ---------------------------------------------------------------------------
# Fake NAPALM connection
# ---------------------------------------------------------------------------

FAKE_DIFF = "--- running\n+++ candidate\n@@ -1 +1 @@\n-description old\n+description new"


class FakeNapalmCiscoConn:
    """Fake NAPALM IOS connection. No socket opened; call tracking only.

    post_diff controls what compare_config returns after commit_config is
    called — set it to a non-empty string to simulate an incomplete commit.
    """

    def __init__(self, post_diff=""):
        self.loaded_configs: list[str] = []
        self.committed = False
        self.discarded_count = 0
        self.rolled_back = False
        self._post_diff = post_diff
        self._commit_done = False

    def open(self):
        pass

    def close(self):
        pass

    def load_merge_candidate(self, config=None):
        self.loaded_configs.append(config)

    def compare_config(self):
        if self._commit_done:
            return self._post_diff
        return FAKE_DIFF

    def commit_config(self):
        self.committed = True
        self._commit_done = True

    def discard_config(self):
        self.discarded_count += 1

    def rollback(self):
        self.rolled_back = True


DEVICE = {
    "name": "cisco-sw-01",
    "hostname": "192.168.5.50",
    "username": "admin",
    "password": "admin",
}


# ---------------------------------------------------------------------------
# _render_restore_intent — supported fields (single-space IOS indent)
# ---------------------------------------------------------------------------

def test_render_interface_description():
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description", "intent": "Uplink to core"}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/1\n description Uplink to core"


def test_render_interface_description_empty_string():
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description", "intent": ""}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/1\n description "


def test_render_interface_enabled_true():
    drift = {"object": "interface:GigabitEthernet1/0/2", "field": "enabled", "intent": True}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/2\n no shutdown"


def test_render_interface_enabled_false():
    drift = {"object": "interface:GigabitEthernet1/0/2", "field": "enabled", "intent": False}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/2\n shutdown"


def test_render_interface_untagged_vlan():
    drift = {"object": "interface:GigabitEthernet1/0/3", "field": "untagged_vlan", "intent": 10}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/3\n switchport access vlan 10"


def test_render_interface_tagged_vlans():
    drift = {"object": "interface:GigabitEthernet1/0/4", "field": "tagged_vlans", "intent": [10, 20, 30]}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/4\n switchport trunk allowed vlan 10,20,30"


def test_render_interface_tagged_vlans_empty():
    drift = {"object": "interface:GigabitEthernet1/0/4", "field": "tagged_vlans", "intent": []}
    assert _render_restore_intent(drift) == "interface GigabitEthernet1/0/4\n switchport trunk allowed vlan "


def test_render_vlan_name():
    drift = {"object": "vlan:20", "field": "name", "intent": "voice"}
    assert _render_restore_intent(drift) == "vlan 20\n name voice"


# ---------------------------------------------------------------------------
# _render_restore_intent — unsupported fields raise NotImplementedError
# ---------------------------------------------------------------------------

def test_render_interface_ip_addresses_raises():
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "ip_addresses", "intent": ["10.0.0.1/30"]}
    with pytest.raises(NotImplementedError, match="ip_addresses"):
        _render_restore_intent(drift)


def test_render_interface_mode_raises():
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "mode", "intent": "access"}
    with pytest.raises(NotImplementedError, match="mode"):
        _render_restore_intent(drift)


def test_render_bgp_neighbor_raises():
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "description", "intent": "peer"}
    with pytest.raises(NotImplementedError, match="bgp_neighbor"):
        _render_restore_intent(drift)


def test_render_vlan_unsupported_field_raises():
    drift = {"object": "vlan:10", "field": "state", "intent": "active"}
    with pytest.raises(NotImplementedError, match="state"):
        _render_restore_intent(drift)


# ---------------------------------------------------------------------------
# _block_mgmt_interface — IOS-XE management interface names
# ---------------------------------------------------------------------------

def test_block_mgmt_interface_raises_for_gigabitethernet0():
    drift = {"object": "interface:GigabitEthernet0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="GigabitEthernet0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_raises_for_gigabitethernet0_slash_0():
    drift = {"object": "interface:GigabitEthernet0/0", "field": "enabled", "intent": True}
    with pytest.raises(RemediationBlockedError, match="GigabitEthernet0/0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_raises_for_management0():
    drift = {"object": "interface:Management0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="Management0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_raises_for_management1():
    drift = {"object": "interface:Management1", "field": "enabled", "intent": True}
    with pytest.raises(RemediationBlockedError, match="Management1"):
        _block_mgmt_interface(drift)


def test_block_mgmt_interface_allows_data_interface():
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description", "intent": "Uplink"}
    _block_mgmt_interface(drift)  # must not raise


# ---------------------------------------------------------------------------
# apply — restore_intent, dry_run=True
# ---------------------------------------------------------------------------

def test_apply_restore_intent_dry_run(monkeypatch):
    fake = FakeNapalmCiscoConn()
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: fake)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "Uplink", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert isinstance(result, ApplyResult)
    assert result.applied is False
    assert result.transport == "cli"
    assert "GigabitEthernet1/0/1" in result.rendered_commands
    assert result.dry_run_diff == FAKE_DIFF
    assert fake.discarded_count == 1
    assert fake.committed is False


# ---------------------------------------------------------------------------
# apply — restore_intent, dry_run=False (commit, clean post-diff)
# ---------------------------------------------------------------------------

def test_apply_restore_intent_commit_clean(monkeypatch):
    fake = FakeNapalmCiscoConn(post_diff="")  # clean commit
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: fake)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "enabled",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "enabled",
             "intent": True, "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=False)

    assert result.applied is True
    assert fake.committed is True
    assert fake.discarded_count == 1   # post-commit verification discard
    assert fake.rolled_back is False   # clean commit, no rollback


# ---------------------------------------------------------------------------
# apply — post-commit verification triggers warning and rollback
# ---------------------------------------------------------------------------

def test_apply_post_commit_mismatch_logs_warning_and_attempts_rollback(
    monkeypatch, caplog
):
    residual_diff = "+interface GigabitEthernet1/0/1\n+ description Uplink"
    fake = FakeNapalmCiscoConn(post_diff=residual_diff)
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: fake)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "Uplink", "drift_kind": "value_mismatch"}

    with caplog.at_level(logging.WARNING, logger="netdrift.appliers.cisco"):
        result = apply(remediation, drift, DEVICE, dry_run=False)

    assert result.applied is True
    assert fake.rolled_back is True
    assert "Post-commit diff is non-empty" in caplog.text


def test_apply_post_commit_rollback_failure_logs_warning(monkeypatch, caplog):
    """Rollback failure is swallowed and logged — must not propagate."""
    fake = FakeNapalmCiscoConn(post_diff="+ some residual diff")
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: fake)

    def _failing_rollback():
        raise RuntimeError("archive not configured")

    fake.rollback = _failing_rollback

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "Uplink", "drift_kind": "value_mismatch"}

    with caplog.at_level(logging.WARNING, logger="netdrift.appliers.cisco"):
        result = apply(remediation, drift, DEVICE, dry_run=False)  # must not raise

    assert result.applied is True
    assert "rollback() failed" in caplog.text


# ---------------------------------------------------------------------------
# apply — raw_snippet
# ---------------------------------------------------------------------------

def test_apply_raw_snippet_dry_run(monkeypatch):
    fake = FakeNapalmCiscoConn()
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: fake)

    snippet = "interface GigabitEthernet1/0/1\n description Fixed"
    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {"cisco_iosxe": {"transport": "cli", "body": snippet}},
    }
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "Fixed", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert result.applied is False
    assert result.rendered_commands == snippet
    assert fake.loaded_configs[0] == snippet


def test_apply_raw_snippet_missing_platform_raises(monkeypatch):
    fake = FakeNapalmCiscoConn()
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: fake)

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {"arista_eos": {"transport": "cli", "body": "!"}},
    }
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}

    with pytest.raises(ValueError, match="cisco_iosxe"):
        apply(remediation, drift, DEVICE)


# ---------------------------------------------------------------------------
# apply — null kind and unknown kind
# ---------------------------------------------------------------------------

def test_apply_null_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: FakeNapalmCiscoConn())
    remediation = {"kind": None}
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="null"):
        apply(remediation, drift, DEVICE)


def test_apply_unknown_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: FakeNapalmCiscoConn())
    remediation = {"kind": "magic"}
    drift = {"object": "interface:GigabitEthernet1/0/1", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="magic"):
        apply(remediation, drift, DEVICE)


# ---------------------------------------------------------------------------
# apply — blocked cases (check_blocked + mgmt interface)
# ---------------------------------------------------------------------------

def test_apply_blocked_session_state(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: FakeNapalmCiscoConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "bgp_neighbor", "field": "session_state",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "session_state",
             "intent": "established", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_management_interface_gigabitethernet0(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: FakeNapalmCiscoConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:GigabitEthernet0", "field": "description",
             "intent": "mgmt", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError, match="GigabitEthernet0"):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_management_interface_gigabitethernet0_slash_0(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.cisco._napalm_conn", lambda device: FakeNapalmCiscoConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:GigabitEthernet0/0", "field": "description",
             "intent": "mgmt", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError, match="GigabitEthernet0/0"):
        apply(remediation, drift, DEVICE)
