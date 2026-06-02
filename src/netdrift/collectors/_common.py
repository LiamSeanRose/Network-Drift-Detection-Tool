"""collectors/_common.py — small helpers shared across NAPALM-based collectors.

The Arista, Cisco, and Juniper collectors all consume NAPALM's
``get_interfaces_ip()`` output, which has one fixed shape, so the CIDR-list
transform is identical for all three. Keeping a single definition here avoids
three copies drifting apart. (The Nokia collector reads IPs from gNMI
subinterface dicts, a different shape, so it keeps its own builder.)
"""


def build_ip_list(ip_raw: dict) -> list[str]:
    """Shape a NAPALM ``get_interfaces_ip()`` entry into a sorted CIDR list.

    NAPALM returns ``{"ipv4": {"10.0.0.1": {"prefix_length": 30}}}`` per
    interface. An interface with no IPs is absent from the getter's output, so an
    empty input dict yields ``[]``.
    """
    ips = []
    for address, detail in ip_raw.get("ipv4", {}).items():
        ips.append(f"{address}/{detail['prefix_length']}")
    return sorted(ips)


def normalize_area(area) -> str:
    """Normalize an OSPF area id to dotted-decimal form (schema Rule 10).

    Devices report areas inconsistently: a bare integer (``0`` or ``"0"``), or
    already-dotted (``"0.0.0.0"``). The schema wants dotted-decimal in every case
    so intent and reality compare like-for-like. Accepts int or str input.

    - ``""`` / ``None`` -> ``""`` (area not present).
    - already dotted -> returned unchanged.
    - bare integer -> converted to dotted (``0`` -> ``"0.0.0.0"``).
    - anything non-numeric and non-dotted -> returned as-is rather than raising,
      so one odd value never breaks a whole collection run.
    """
    if area == "" or area is None:
        return ""
    s = str(area).strip()
    if not s:
        return ""
    if "." in s:
        return s
    try:
        n = int(s)
    except ValueError:
        return s
    return f"{(n >> 24) & 0xFF}.{(n >> 16) & 0xFF}.{(n >> 8) & 0xFF}.{n & 0xFF}"
