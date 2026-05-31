def fingerprint(drift: dict) -> str:
    """Return a stable string key for a drift record, stripping variable parts.

    Strips: device name, specific object identifier (IP, interface name, VLAN ID),
    intent/reality values, and timestamp. Keeps: object type, field, drift kind.
    This means the same structural problem on any device or identifier maps to
    the same fingerprint and can be matched against a stored known issue.
    """
    object_type = drift["object"].split(":")[0]
    return f"{object_type}|{drift['field']}|{drift['drift_kind']}"
