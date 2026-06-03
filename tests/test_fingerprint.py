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


# ---------------------------------------------------------------------------
# v5.0 — fuzzy / semantic fingerprint matching (feature-flagged, default off)
# ---------------------------------------------------------------------------

from netdrift.fingerprint import (  # noqa: E402
    DEFAULT_FUZZY_THRESHOLD,
    fuzzy_fingerprint,
    fuzzy_match,
    fuzzy_matching_enabled,
    fuzzy_similarity,
    normalize_object_ref,
)


def test_fuzzy_matching_off_by_default(monkeypatch):
    monkeypatch.delenv("FUZZY_MATCHING_ENABLED", raising=False)
    assert fuzzy_matching_enabled() is False


def test_fuzzy_matching_flag_reads_env(monkeypatch):
    monkeypatch.setenv("FUZZY_MATCHING_ENABLED", "true")
    assert fuzzy_matching_enabled() is True


def test_normalize_tokenizes_variable_identifiers():
    assert normalize_object_ref("bgp_neighbor:10.0.0.2") == "bgp_neighbor:{IP}"
    assert normalize_object_ref("ospf_adjacency:2.2.2.2") == "ospf_adjacency:{ROUTER_ID}"
    assert normalize_object_ref("vlan:10") == "vlan:{VLAN_ID}"


def test_normalize_keeps_interface_class_drops_slot_numbers():
    assert normalize_object_ref("interface:TenGigabitEthernet1/1/4") == "interface:TenGigabitEthernet"
    assert normalize_object_ref("interface:Ethernet1") == "interface:Ethernet"
    assert normalize_object_ref("tunnel:Tunnel0") == "tunnel:Tunnel"


def test_normalize_leaves_identifierless_objects_unchanged():
    assert normalize_object_ref("config") == "config"
    assert normalize_object_ref("device") == "device"


def test_dod_structurally_identical_bgp_drift_matches_across_peers():
    # v5.0 DoD: a known issue for one peer IP matches the same drift on another.
    a = fuzzy_fingerprint({"object": "bgp_neighbor:10.0.0.1",
                           "field": "session_state", "drift_kind": "value_mismatch"})
    b = fuzzy_fingerprint({"object": "bgp_neighbor:10.1.1.1",
                           "field": "session_state", "drift_kind": "value_mismatch"})
    assert a == b
    assert fuzzy_similarity(a, b) == 1.0


def test_fuzzy_similarity_partial_for_one_differing_component():
    # Same object+field, different drift_kind → 3 of 4 tokens shared.
    a = "bgp_neighbor:{IP}|session_state|value_mismatch"
    b = "bgp_neighbor:{IP}|session_state|missing_in_reality"
    score = fuzzy_similarity(a, b)
    assert 0.0 < score < 1.0
    assert round(score, 2) == 0.6


def test_fuzzy_similarity_disjoint_is_zero():
    assert fuzzy_similarity("vlan:{VLAN_ID}|name|value_mismatch",
                            "tunnel:Tunnel|tunnel_state|missing_in_intent") == 0.0


def test_fuzzy_match_returns_best_above_threshold():
    target = "bgp_neighbor:{IP}|session_state|value_mismatch"
    candidates = [
        "vlan:{VLAN_ID}|name|value_mismatch",                 # disjoint
        "bgp_neighbor:{IP}|session_state|missing_in_reality",  # 0.6
        "bgp_neighbor:{IP}|session_state|value_mismatch",      # exact 1.0
    ]
    best, conf = fuzzy_match(target, candidates)
    assert best == "bgp_neighbor:{IP}|session_state|value_mismatch"
    assert conf == 1.0


def test_fuzzy_match_returns_none_below_threshold():
    target = "bgp_neighbor:{IP}|session_state|value_mismatch"
    candidates = ["vlan:{VLAN_ID}|name|value_mismatch"]  # well below 0.7
    best, conf = fuzzy_match(target, candidates, threshold=DEFAULT_FUZZY_THRESHOLD)
    assert best is None
    assert conf == 0.0
