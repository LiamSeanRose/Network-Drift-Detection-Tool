"""tests/test_junos_applier.py — Juniper JunOS applier unit tests.

Follows the same structure as test_arista_applier.py and test_cisco_applier.py.
FakeNapalmJunosConn stands in for NAPALM's JunOS driver — no socket opened.
"""

import pytest

from netdrift.appliers.junos import (
    _apply_via_napalm,
    _block_mgmt_interface,
    _render_restore_intent,
    apply,
)
from netdrift.appliers.base import ApplyResult, RemediationBlockedError


# ---------------------------------------------------------------------------
# Fake NAPALM connection
# ---------------------------------------------------------------------------

class FakeNapalmJunosConn:
    def __init__(self):
        self._diff = "some diff"
        self.committed = False
        self.discarded = False
        self.loaded_config = None

    def load_merge_candidate(self, config=None):
        self.loaded_config = config

    def compare_config(self):
        return self._diff

    def commit_config(self):
        self.committed = True

    def discard_config(self):
        self.discarded = True

    def close(self):
        pass


DEVICE = {
    "name": "junos-sw-01",
    "hostname": "172.20.20.31",
    "username": "admin",
    "password": "JunosPw1!",
}


# ---------------------------------------------------------------------------
# _render_restore_intent — interface fields
# ---------------------------------------------------------------------------

def test_render_description():
    drift = {"object": "interface:ge-0/0/0", "field": "description", "intent": "Uplink"}
    text = _render_restore_intent(drift)
    assert "ge-0/0/0" in text
    assert "Uplink" in text


def test_render_description_empty_intent():
    drift = {"object": "interface:ge-0/0/0", "field": "description", "intent": ""}
    text = _render_restore_intent(drift)
    assert "ge-0/0/0" in text


def test_render_enabled_true():
    drift = {"object": "interface:ge-0/0/0", "field": "enabled", "intent": True}
    text = _render_restore_intent(drift)
    assert "ge-0/0/0" in text
    # Enable = delete the disable statement
    assert "delete" in text or "disable" not in text.replace("delete: disable", "")


def test_render_enabled_false():
    drift = {"object": "interface:ge-0/0/0", "field": "enabled", "intent": False}
    text = _render_restore_intent(drift)
    assert "ge-0/0/0" in text
    assert "disable" in text


def test_render_untagged_vlan():
    drift = {"object": "interface:ge-0/0/1", "field": "untagged_vlan", "intent": 10}
    text = _render_restore_intent(drift)
    assert "ge-0/0/1" in text
    assert "10" in text
    assert "ethernet-switching" in text


def test_render_tagged_vlans():
    drift = {"object": "interface:ge-0/0/1", "field": "tagged_vlans", "intent": [10, 20]}
    text = _render_restore_intent(drift)
    assert "ge-0/0/1" in text
    assert "10" in text
    assert "20" in text


def test_render_vlan_name():
    drift = {"object": "vlan:10", "field": "name", "intent": "users"}
    text = _render_restore_intent(drift)
    assert "10" in text
    assert "users" in text


def test_render_unsupported_interface_field_raises():
    drift = {"object": "interface:ge-0/0/0", "field": "ip_addresses", "intent": []}
    with pytest.raises(NotImplementedError, match="ip_addresses"):
        _render_restore_intent(drift)


def test_render_unsupported_object_raises():
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "remote_as", "intent": 65000}
    with pytest.raises(NotImplementedError):
        _render_restore_intent(drift)


# ---------------------------------------------------------------------------
# _block_mgmt_interface
# ---------------------------------------------------------------------------

def test_block_mgmt_fxp0():
    drift = {"object": "interface:fxp0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="fxp0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_em0():
    drift = {"object": "interface:em0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="em0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_fxp0_subinterface():
    drift = {"object": "interface:fxp0.0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="fxp0.0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_em0_subinterface():
    drift = {"object": "interface:em0.0", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError, match="em0.0"):
        _block_mgmt_interface(drift)


def test_block_mgmt_fxp1_prefix():
    # Any fxp-prefixed interface is management (fxp1 exists on some platforms).
    drift = {"object": "interface:fxp1", "field": "description", "intent": "mgmt"}
    with pytest.raises(RemediationBlockedError):
        _block_mgmt_interface(drift)


def test_block_mgmt_allows_data_interface():
    drift = {"object": "interface:ge-0/0/0", "field": "description", "intent": "uplink"}
    _block_mgmt_interface(drift)  # must not raise


def test_block_mgmt_allows_ae_interface():
    drift = {"object": "interface:ae0", "field": "description", "intent": "lag"}
    _block_mgmt_interface(drift)  # must not raise


# ---------------------------------------------------------------------------
# _apply_via_napalm — dry_run=True
# ---------------------------------------------------------------------------

def test_apply_dry_run_does_not_commit():
    conn = FakeNapalmJunosConn()
    result = _apply_via_napalm(conn, "interfaces { ge-0/0/0 { description x; } }", dry_run=True)
    assert result.applied is False
    assert conn.committed is False
    assert conn.discarded is True


def test_apply_dry_run_returns_diff():
    conn = FakeNapalmJunosConn()
    result = _apply_via_napalm(conn, "interfaces { }", dry_run=True)
    assert result.dry_run_diff == "some diff"
    assert result.transport == "cli"


def test_apply_dry_run_returns_rendered_commands():
    config = "interfaces { ge-0/0/0 { description test; } }"
    conn = FakeNapalmJunosConn()
    result = _apply_via_napalm(conn, config, dry_run=True)
    assert result.rendered_commands == config


# ---------------------------------------------------------------------------
# _apply_via_napalm — dry_run=False (commit)
# ---------------------------------------------------------------------------

def test_apply_commit_calls_commit():
    conn = FakeNapalmJunosConn()
    result = _apply_via_napalm(conn, "interfaces { }", dry_run=False)
    assert result.applied is True
    assert conn.committed is True
    assert conn.discarded is False


def test_apply_commit_returns_transport_cli():
    conn = FakeNapalmJunosConn()
    result = _apply_via_napalm(conn, "interfaces { }", dry_run=False)
    assert result.transport == "cli"


# ---------------------------------------------------------------------------
# apply() — full flow via monkeypatched _napalm_conn
# ---------------------------------------------------------------------------

def test_apply_restore_intent_dry_run(monkeypatch):
    fake_conn = FakeNapalmJunosConn()
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: fake_conn)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:ge-0/0/0", "field": "description",
             "intent": "Uplink", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert isinstance(result, ApplyResult)
    assert result.applied is False
    assert result.transport == "cli"
    assert fake_conn.committed is False


def test_apply_restore_intent_commit(monkeypatch):
    fake_conn = FakeNapalmJunosConn()
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: fake_conn)

    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "enabled",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:ge-0/0/0", "field": "enabled",
             "intent": True, "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=False)

    assert result.applied is True
    assert fake_conn.committed is True


def test_apply_raw_snippet_dry_run(monkeypatch):
    fake_conn = FakeNapalmJunosConn()
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: fake_conn)

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {
            "juniper_junos": {"transport": "cli", "body": "interfaces { ge-0/0/0 { description x; } }"},
        },
    }
    drift = {"object": "interface:ge-0/0/0", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}

    result = apply(remediation, drift, DEVICE, dry_run=True)

    assert result.applied is False
    assert "ge-0/0/0" in result.rendered_commands


def test_apply_raw_snippet_missing_platform_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: FakeNapalmJunosConn())

    remediation = {
        "kind": "raw_snippet",
        "schema_version": 1,
        "by_platform": {"arista_eos": {"transport": "cli", "body": "!"}},
    }
    drift = {"object": "interface:ge-0/0/0", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}

    with pytest.raises(ValueError, match="juniper_junos"):
        apply(remediation, drift, DEVICE)


def test_apply_null_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: FakeNapalmJunosConn())
    remediation = {"kind": None}
    drift = {"object": "interface:ge-0/0/0", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="null"):
        apply(remediation, drift, DEVICE)


def test_apply_unknown_kind_raises(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: FakeNapalmJunosConn())
    remediation = {"kind": "magic"}
    drift = {"object": "interface:ge-0/0/0", "field": "description",
             "intent": "x", "drift_kind": "value_mismatch"}
    with pytest.raises(ValueError, match="magic"):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_session_state(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: FakeNapalmJunosConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "bgp_neighbor", "field": "session_state",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "bgp_neighbor:10.0.0.2", "field": "session_state",
             "intent": "established", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_fxp0(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: FakeNapalmJunosConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:fxp0", "field": "description",
             "intent": "oob-mgmt", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError, match="fxp0"):
        apply(remediation, drift, DEVICE)


def test_apply_blocked_em0(monkeypatch):
    monkeypatch.setattr("netdrift.appliers.junos._napalm_conn",
                        lambda device: FakeNapalmJunosConn())
    remediation = {"kind": "restore_intent", "schema_version": 1,
                   "object_type": "interface", "field": "description",
                   "drift_kinds": ["value_mismatch"]}
    drift = {"object": "interface:em0", "field": "description",
             "intent": "oob-mgmt", "drift_kind": "value_mismatch"}
    with pytest.raises(RemediationBlockedError, match="em0"):
        apply(remediation, drift, DEVICE)
