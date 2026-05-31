from netdrift.diagnose import diagnose


def _drift(object_str, field, drift_kind):
    return {"object": object_str, "field": field, "drift_kind": drift_kind}


def test_unknown_combination_returns_empty():
    assert diagnose(_drift("interface:Eth1", "nonexistent", "value_mismatch")) == []


def test_interface_missing_in_reality():
    causes = diagnose(_drift("interface:Ethernet1", "_interface", "missing_in_reality"))
    assert len(causes) > 0 and all(isinstance(c, str) for c in causes)


def test_interface_enabled_mismatch():
    assert len(diagnose(_drift("interface:Ethernet1", "enabled", "value_mismatch"))) > 0


def test_interface_ip_mismatch():
    assert len(diagnose(_drift("interface:Ethernet1", "ip_addresses", "value_mismatch"))) > 0


def test_vlan_missing_in_intent():
    assert len(diagnose(_drift("vlan:100", "_vlan", "missing_in_intent"))) > 0


def test_bgp_session_state_mismatch():
    assert len(diagnose(_drift("bgp_neighbor:10.0.0.1", "session_state", "value_mismatch"))) > 0


def test_bgp_remote_as_mismatch():
    assert len(diagnose(_drift("bgp_neighbor:10.0.0.1", "remote_as", "value_mismatch"))) > 0


def test_ospf_missing_in_reality():
    assert len(diagnose(_drift("ospf_adjacency:1.1.1.1", "_ospf_adjacency", "missing_in_reality"))) > 0


def test_ospf_adjacency_state_mismatch():
    assert len(diagnose(_drift("ospf_adjacency:1.1.1.1", "adjacency_state", "value_mismatch"))) > 0


def test_config_drift():
    assert len(diagnose(_drift("config", "running_config", "value_mismatch"))) > 0


def test_interface_mtu_mismatch():
    assert len(diagnose(_drift("interface:Ethernet1", "mtu", "value_mismatch"))) > 0


def test_bgp_password_mismatch():
    assert len(diagnose(_drift("bgp_neighbor:10.0.0.1", "password", "value_mismatch"))) > 0


def test_ospf_network_type_mismatch():
    assert len(diagnose(_drift("ospf_adjacency:1.1.1.1", "network_type", "value_mismatch"))) > 0
