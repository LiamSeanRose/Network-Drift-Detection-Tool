from netdrift.differ import diff


# --- Test helpers ------------------------------------------------------------
# Every interface dict must carry all six schema keys (schema.md Rule 4), and
# every device-state object must carry a top-level `vlans` dict. Rather than
# spell all of that out in every test, these helpers fill in schema-complete
# defaults so each test can specify only the field it actually cares about.

def iface(**overrides):
    """A schema-complete interface dict. Defaults to an enabled routed
    interface with no description, IPs, or VLANs; override any field."""
    base = {
        "description": "",
        "enabled": True,
        "ip_addresses": [],
        "mode": "routed",
        "untagged_vlan": None,
        "tagged_vlans": [],
    }
    base.update(overrides)
    return base


def state(interfaces=None, vlans=None, bgp_neighbors=None, ospf=None):
    """A schema-complete device-state object wrapping the given interfaces
    and vlans (both default to empty). v0.3 adds bgp_neighbors and ospf;
    both default to the "no routing on this device" empty shape."""
    return {
        "interfaces": interfaces or {},
        "vlans": vlans or {},
        "bgp_neighbors": bgp_neighbors or {},
        "ospf": ospf or {"adjacencies": {}},
    }


# --- v0.1 fields: description, enabled, ip_addresses -------------------------

def test_identical_inputs_produce_no_drift():
    intent = state({"Ethernet1": iface(description="Uplink")})
    reality = state({"Ethernet1": iface(description="Uplink")})
    assert diff(intent, reality) == []


def test_enabled_flip_produces_one_drift():
    intent = state({"Ethernet1": iface(enabled=True)})
    reality = state({"Ethernet1": iface(enabled=False)})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "enabled"


def test_ip_address_change_produces_one_drift():
    intent = state({"Ethernet1": iface(ip_addresses=["10.1.1.5/24"])})
    reality = state({"Ethernet1": iface(ip_addresses=["10.1.1.9/24"])})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "ip_addresses"


def test_interface_missing_in_reality():
    intent = state({"Ethernet1": iface(description="Uplink")})
    reality = state({})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "_interface"
    assert result[0]["drift_kind"] == "missing_in_reality"


def test_interface_missing_in_intent():
    intent = state({})
    reality = state({"Ethernet9": iface(description="Mystery")})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "_interface"
    assert result[0]["drift_kind"] == "missing_in_intent"


def test_enabled_intent_up_reality_down_is_critical():
    intent = state({"Ethernet1": iface(enabled=True)})
    reality = state({"Ethernet1": iface(enabled=False)})
    result = diff(intent, reality)
    assert result[0]["severity"] == "critical"
    assert result[0]["drift_kind"] == "value_mismatch"


def test_enabled_intent_down_reality_up_is_warning():
    intent = state({"Ethernet1": iface(enabled=False)})
    reality = state({"Ethernet1": iface(enabled=True)})
    result = diff(intent, reality)
    assert result[0]["severity"] == "warning"


def test_description_drift_is_info():
    intent = state({"Ethernet1": iface(description="Old")})
    reality = state({"Ethernet1": iface(description="New")})
    result = diff(intent, reality)
    assert result[0]["severity"] == "info"


def test_ip_drift_is_warning():
    intent = state({"Ethernet1": iface(ip_addresses=["10.0.0.1/24"])})
    reality = state({"Ethernet1": iface(ip_addresses=["10.0.0.2/24"])})
    result = diff(intent, reality)
    assert result[0]["severity"] == "warning"


# --- v0.2 interface fields: mode, untagged_vlan, tagged_vlans ----------------

def test_mode_mismatch_is_warning():
    intent = state({"Ethernet2": iface(mode="access", untagged_vlan=10)})
    reality = state({"Ethernet2": iface(mode="tagged", untagged_vlan=10)})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "mode"
    assert result[0]["drift_kind"] == "value_mismatch"
    assert result[0]["severity"] == "warning"


def test_untagged_vlan_mismatch_is_warning():
    intent = state({"Ethernet2": iface(mode="access", untagged_vlan=10)})
    reality = state({"Ethernet2": iface(mode="access", untagged_vlan=99)})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "untagged_vlan"
    assert result[0]["intent"] == 10
    assert result[0]["reality"] == 99
    assert result[0]["severity"] == "warning"


def test_tagged_vlans_mismatch_is_warning():
    intent = state({"Ethernet3": iface(mode="tagged", tagged_vlans=[10, 20])})
    reality = state({"Ethernet3": iface(mode="tagged", tagged_vlans=[10, 20, 30])})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "tagged_vlans"
    assert result[0]["severity"] == "warning"


# --- v0.2 top-level vlans block ----------------------------------------------

def test_vlan_missing_in_reality_is_warning():
    intent = state(vlans={"10": {"name": "users"}})
    reality = state(vlans={})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["object"] == "vlan:10"
    assert result[0]["field"] == "_vlan"
    assert result[0]["drift_kind"] == "missing_in_reality"
    assert result[0]["severity"] == "warning"


def test_vlan_missing_in_intent_is_info():
    intent = state(vlans={})
    reality = state(vlans={"20": {"name": "voice"}})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["object"] == "vlan:20"
    assert result[0]["field"] == "_vlan"
    assert result[0]["drift_kind"] == "missing_in_intent"
    assert result[0]["severity"] == "info"


def test_vlan_name_mismatch_is_info():
    intent = state(vlans={"20": {"name": "voice"}})
    reality = state(vlans={"20": {"name": "Voice-VLAN"}})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["object"] == "vlan:20"
    assert result[0]["field"] == "name"
    assert result[0]["intent"] == "voice"
    assert result[0]["reality"] == "Voice-VLAN"
    assert result[0]["drift_kind"] == "value_mismatch"
    assert result[0]["severity"] == "info"