from datetime import datetime, timezone


def _now():
    """Current time as an ISO 8601 UTC string with a Z suffix, per schema rule 2."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def diff(intent, reality):
    drifts = []

    intent_ifaces = intent["interfaces"]
    reality_ifaces = reality["interfaces"]

    for iface_name, intent_data in intent_ifaces.items():
        if iface_name not in reality_ifaces:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "_interface",
                "intent": "present",
                "reality": "absent",
                "drift_kind": "missing_in_reality",
                "severity": "critical",
                "detected_at": _now(),
            })
            continue

        reality_data = reality_ifaces[iface_name]

        if intent_data["enabled"] != reality_data["enabled"]:
            # Direction matters: should-be-up-but-down is an outage (critical);
            # should-be-down-but-up is sloppy but live (warning).
            if intent_data["enabled"] is True:
                severity = "critical"
            else:
                severity = "warning"
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "enabled",
                "intent": intent_data["enabled"],
                "reality": reality_data["enabled"],
                "drift_kind": "value_mismatch",
                "severity": severity,
                "detected_at": _now(),
            })

        if intent_data["description"] != reality_data["description"]:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "description",
                "intent": intent_data["description"],
                "reality": reality_data["description"],
                "drift_kind": "value_mismatch",
                "severity": "info",
                "detected_at": _now(),
            })

        if intent_data["ip_addresses"] != reality_data["ip_addresses"]:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "ip_addresses",
                "intent": intent_data["ip_addresses"],
                "reality": reality_data["ip_addresses"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

        # v0.2 layer-2 fields. mode / untagged_vlan / tagged_vlans all follow
        # the same value_mismatch pattern as the fields above; all warning per
        # schema.md Section 7.
        if intent_data["mode"] != reality_data["mode"]:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "mode",
                "intent": intent_data["mode"],
                "reality": reality_data["mode"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

        if intent_data["untagged_vlan"] != reality_data["untagged_vlan"]:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "untagged_vlan",
                "intent": intent_data["untagged_vlan"],
                "reality": reality_data["untagged_vlan"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

        if intent_data["tagged_vlans"] != reality_data["tagged_vlans"]:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "tagged_vlans",
                "intent": intent_data["tagged_vlans"],
                "reality": reality_data["tagged_vlans"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

    for iface_name in reality_ifaces:
        if iface_name not in intent_ifaces:
            drifts.append({
                "object": "interface:" + iface_name,
                "field": "_interface",
                "intent": "absent",
                "reality": "present",
                "drift_kind": "missing_in_intent",
                "severity": "warning",
                "detected_at": _now(),
            })

    # v0.2 top-level VLAN block. The `vlans` dict is keyed by VLAN id as a
    # STRING (schema Rule 7). A VLAN present on only one side is a missing_in_*
    # record with the sentinel field "_vlan"; a VLAN on both sides whose name
    # differs is a name value_mismatch. Severities per schema.md Section 7.
    intent_vlans = intent["vlans"]
    reality_vlans = reality["vlans"]

    for vlan_id, intent_vlan in intent_vlans.items():
        if vlan_id not in reality_vlans:
            drifts.append({
                "object": "vlan:" + vlan_id,
                "field": "_vlan",
                "intent": "present",
                "reality": "absent",
                "drift_kind": "missing_in_reality",
                "severity": "warning",
                "detected_at": _now(),
            })
            continue

        reality_vlan = reality_vlans[vlan_id]
        if intent_vlan["name"] != reality_vlan["name"]:
            drifts.append({
                "object": "vlan:" + vlan_id,
                "field": "name",
                "intent": intent_vlan["name"],
                "reality": reality_vlan["name"],
                "drift_kind": "value_mismatch",
                "severity": "info",
                "detected_at": _now(),
            })

    for vlan_id in reality_vlans:
        if vlan_id not in intent_vlans:
            drifts.append({
                "object": "vlan:" + vlan_id,
                "field": "_vlan",
                "intent": "absent",
                "reality": "present",
                "drift_kind": "missing_in_intent",
                "severity": "info",
                "detected_at": _now(),
            })

    # v0.3 top-level BGP neighbors block. Keyed by neighbor IP as a STRING
    # (schema.md Rule for v0.3, mirrors VLAN keying). A neighbor present on
    # one side and absent on the other is a missing_in_* record with the
    # sentinel field "_bgp_neighbor". A neighbor on both sides whose fields
    # differ is a per-field value_mismatch. Severities per schema.md Section 7.
    intent_bgp = intent["bgp_neighbors"]
    reality_bgp = reality["bgp_neighbors"]

    for peer_ip, intent_peer in intent_bgp.items():
        if peer_ip not in reality_bgp:
            drifts.append({
                "object": "bgp_neighbor:" + peer_ip,
                "field": "_bgp_neighbor",
                "intent": "present",
                "reality": "absent",
                "drift_kind": "missing_in_reality",
                "severity": "warning",
                "detected_at": _now(),
            })
            continue

        reality_peer = reality_bgp[peer_ip]

        if intent_peer["remote_as"] != reality_peer["remote_as"]:
            drifts.append({
                "object": "bgp_neighbor:" + peer_ip,
                "field": "remote_as",
                "intent": intent_peer["remote_as"],
                "reality": reality_peer["remote_as"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

        if intent_peer["enabled"] != reality_peer["enabled"]:
            drifts.append({
                "object": "bgp_neighbor:" + peer_ip,
                "field": "enabled",
                "intent": intent_peer["enabled"],
                "reality": reality_peer["enabled"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

        if intent_peer["description"] != reality_peer["description"]:
            drifts.append({
                "object": "bgp_neighbor:" + peer_ip,
                "field": "description",
                "intent": intent_peer["description"],
                "reality": reality_peer["description"],
                "drift_kind": "value_mismatch",
                "severity": "info",
                "detected_at": _now(),
            })

        if intent_peer["session_state"] != reality_peer["session_state"]:
            drifts.append({
                "object": "bgp_neighbor:" + peer_ip,
                "field": "session_state",
                "intent": intent_peer["session_state"],
                "reality": reality_peer["session_state"],
                "drift_kind": "value_mismatch",
                "severity": "warning",
                "detected_at": _now(),
            })

    for peer_ip in reality_bgp:
        if peer_ip not in intent_bgp:
            drifts.append({
                "object": "bgp_neighbor:" + peer_ip,
                "field": "_bgp_neighbor",
                "intent": "absent",
                "reality": "present",
                "drift_kind": "missing_in_intent",
                "severity": "info",
                "detected_at": _now(),
            })

    return drifts