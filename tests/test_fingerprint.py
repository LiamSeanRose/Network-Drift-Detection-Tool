from netdrift.fingerprint import fingerprint


def test_strips_device_and_identifier():
    # Same field drift on two different devices/interfaces → same fingerprint
    d1 = {"object": "interface:Ethernet1", "field": "enabled", "drift_kind": "value_mismatch"}
    d2 = {"object": "interface:Ethernet99", "field": "enabled", "drift_kind": "value_mismatch"}
    assert fingerprint(d1) == fingerprint(d2)


def test_different_fields_differ():
    d1 = {"object": "interface:Ethernet1", "field": "enabled", "drift_kind": "value_mismatch"}
    d2 = {"object": "interface:Ethernet1", "field": "description", "drift_kind": "value_mismatch"}
    assert fingerprint(d1) != fingerprint(d2)


def test_different_object_types_differ():
    d1 = {"object": "interface:Ethernet1", "field": "enabled", "drift_kind": "value_mismatch"}
    d2 = {"object": "bgp_neighbor:10.0.0.1", "field": "enabled", "drift_kind": "value_mismatch"}
    assert fingerprint(d1) != fingerprint(d2)


def test_different_drift_kinds_differ():
    d1 = {"object": "interface:Ethernet1", "field": "_interface", "drift_kind": "missing_in_reality"}
    d2 = {"object": "interface:Ethernet1", "field": "_interface", "drift_kind": "missing_in_intent"}
    assert fingerprint(d1) != fingerprint(d2)


def test_bgp_neighbor_strips_ip():
    d1 = {"object": "bgp_neighbor:10.0.0.1", "field": "session_state", "drift_kind": "value_mismatch"}
    d2 = {"object": "bgp_neighbor:192.168.1.1", "field": "session_state", "drift_kind": "value_mismatch"}
    assert fingerprint(d1) == fingerprint(d2)


def test_config_drift_fingerprint():
    d = {"object": "config", "field": "running_config", "drift_kind": "value_mismatch"}
    fp = fingerprint(d)
    assert isinstance(fp, str) and len(fp) > 0


def test_returns_string():
    d = {"object": "vlan:10", "field": "name", "drift_kind": "value_mismatch"}
    assert isinstance(fingerprint(d), str)
