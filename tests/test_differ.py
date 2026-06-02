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


def tunnel(**overrides):
    """A schema-complete tunnel dict (v4.75). Defaults to an enabled GRE tunnel
    that is operationally up; override any field."""
    base = {
        "type": "gre",
        "source": "192.0.2.1",
        "destination": "198.51.100.1",
        "enabled": True,
        "tunnel_state": "up",
    }
    base.update(overrides)
    return base


def state(interfaces=None, vlans=None, bgp_neighbors=None, ospf=None,
          running_config="", software_version="", tunnels=None):
    """A schema-complete device-state object wrapping the given interfaces
    and vlans (both default to empty). v0.3 adds bgp_neighbors and ospf;
    both default to the "no routing on this device" empty shape. v1.0 adds
    running_config; defaults to "" (no template / no config collected).
    software_version defaults to "" (not tracked). v4.75 adds the optional
    `tunnels` block: when not passed the key is omitted entirely, matching a
    collector/intent with no tunnels (so absent-on-both produces no drift)."""
    base = {
        "interfaces": interfaces or {},
        "vlans": vlans or {},
        "bgp_neighbors": bgp_neighbors or {},
        "ospf": ospf or {"adjacencies": {}},
        "running_config": running_config,
        "software_version": software_version,
    }
    if tunnels is not None:
        base["tunnels"] = tunnels
    return base


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


# --- v1.0 running_config field ----------------------------------------------

CONFIG_A = "hostname core-sw-01\nip routing\n"
CONFIG_B = "hostname core-sw-01\nip routing\nno ip routing\n"


def test_config_match_produces_no_drift():
    intent = state(running_config=CONFIG_A)
    reality = state(running_config=CONFIG_A)
    assert diff(intent, reality) == []


def test_config_mismatch_produces_one_drift_record():
    intent = state(running_config=CONFIG_A)
    reality = state(running_config=CONFIG_B)
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["object"] == "config"
    assert result[0]["field"] == "running_config"
    assert result[0]["drift_kind"] == "value_mismatch"
    assert result[0]["severity"] == "warning"


def test_config_drift_skipped_when_intent_empty():
    intent = state(running_config="")
    reality = state(running_config=CONFIG_A)
    assert diff(intent, reality) == []


def test_config_drift_skipped_when_reality_empty():
    intent = state(running_config=CONFIG_A)
    reality = state(running_config="")
    assert diff(intent, reality) == []


def test_config_normalization_strips_trailing_whitespace():
    intent = state(running_config="hostname core-sw-01  \nip routing\n")
    reality = state(running_config="hostname core-sw-01\nip routing\n")
    assert diff(intent, reality) == []


# --- software_version field --------------------------------------------------

def test_software_version_match_produces_no_drift():
    intent = state(software_version="4.28.0F")
    reality = state(software_version="4.28.0F")
    assert diff(intent, reality) == []


def test_software_version_mismatch_produces_drift():
    intent = state(software_version="4.28.0F")
    reality = state(software_version="4.26.2F")
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["object"] == "device"
    assert result[0]["field"] == "software_version"
    assert result[0]["drift_kind"] == "value_mismatch"
    assert result[0]["severity"] == "warning"
    assert result[0]["intent"] == "4.28.0F"
    assert result[0]["reality"] == "4.26.2F"


def test_software_version_skipped_when_intent_empty():
    intent = state(software_version="")
    reality = state(software_version="4.26.2F")
    assert diff(intent, reality) == []


def test_software_version_skipped_when_reality_empty():
    intent = state(software_version="4.28.0F")
    reality = state(software_version="")
    assert diff(intent, reality) == []


def test_software_version_skipped_when_both_empty():
    assert diff(state(), state()) == []


# --- v4.75 tunnels ----------------------------------------------------------

def test_absent_tunnels_key_on_both_sides_no_drift():
    # Neither side declares the optional tunnels block -> nothing to compare.
    assert diff(state(), state()) == []


def test_empty_tunnels_blocks_no_drift():
    assert diff(state(tunnels={}), state(tunnels={})) == []


def test_identical_tunnels_no_drift():
    intent = state(tunnels={"Tunnel0": tunnel()})
    reality = state(tunnels={"Tunnel0": tunnel()})
    assert diff(intent, reality) == []


def test_tunnel_missing_in_reality_is_critical():
    intent = state(tunnels={"Tunnel0": tunnel()})
    reality = state(tunnels={})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["object"] == "tunnel:Tunnel0"
    assert result[0]["field"] == "_tunnel"
    assert result[0]["drift_kind"] == "missing_in_reality"
    assert result[0]["severity"] == "critical"


def test_tunnel_undocumented_is_info():
    intent = state(tunnels={})
    reality = state(tunnels={"Tunnel0": tunnel()})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "_tunnel"
    assert result[0]["drift_kind"] == "missing_in_intent"
    assert result[0]["severity"] == "info"


def test_tunnel_only_present_intent_uses_get_default():
    # Reality omits the key entirely; an intent tunnel still surfaces as missing.
    intent = state(tunnels={"Tunnel0": tunnel()})
    reality = state()
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["drift_kind"] == "missing_in_reality"


def test_tunnel_type_mismatch_is_warning():
    intent = state(tunnels={"Tunnel0": tunnel(type="gre")})
    reality = state(tunnels={"Tunnel0": tunnel(type="vti")})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "type"
    assert result[0]["severity"] == "warning"


def test_tunnel_source_mismatch_is_warning():
    intent = state(tunnels={"Tunnel0": tunnel(source="192.0.2.1")})
    reality = state(tunnels={"Tunnel0": tunnel(source="192.0.2.2")})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "source"
    assert result[0]["severity"] == "warning"


def test_tunnel_destination_mismatch_is_warning():
    intent = state(tunnels={"Tunnel0": tunnel(destination="198.51.100.1")})
    reality = state(tunnels={"Tunnel0": tunnel(destination="203.0.113.1")})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "destination"
    assert result[0]["severity"] == "warning"


def test_tunnel_enabled_down_when_intent_up_is_critical():
    intent = state(tunnels={"Tunnel0": tunnel(enabled=True)})
    reality = state(tunnels={"Tunnel0": tunnel(enabled=False)})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "enabled"
    assert result[0]["severity"] == "critical"


def test_tunnel_enabled_up_when_intent_down_is_warning():
    intent = state(tunnels={"Tunnel0": tunnel(enabled=False)})
    reality = state(tunnels={"Tunnel0": tunnel(enabled=True)})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "enabled"
    assert result[0]["severity"] == "warning"


def test_tunnel_state_down_when_intent_up_is_critical():
    intent = state(tunnels={"Tunnel0": tunnel(tunnel_state="up")})
    reality = state(tunnels={"Tunnel0": tunnel(tunnel_state="down")})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "tunnel_state"
    assert result[0]["severity"] == "critical"


def test_tunnel_state_up_when_intent_down_is_warning():
    intent = state(tunnels={"Tunnel0": tunnel(tunnel_state="down")})
    reality = state(tunnels={"Tunnel0": tunnel(tunnel_state="up")})
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "tunnel_state"
    assert result[0]["severity"] == "warning"


def test_tunnel_multiple_field_mismatches_each_emit_a_record():
    intent = state(tunnels={"Tunnel0": tunnel(
        type="gre", source="192.0.2.1", destination="198.51.100.1",
        enabled=True, tunnel_state="up")})
    reality = state(tunnels={"Tunnel0": tunnel(
        type="vti", source="192.0.2.9", destination="198.51.100.9",
        enabled=False, tunnel_state="down")})
    result = diff(intent, reality)
    fields = {d["field"] for d in result}
    assert fields == {"type", "source", "destination", "enabled", "tunnel_state"}