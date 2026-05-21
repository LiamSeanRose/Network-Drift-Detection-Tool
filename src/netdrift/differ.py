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

    return drifts