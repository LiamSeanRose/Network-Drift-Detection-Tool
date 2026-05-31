_RULES: dict[tuple[str, str, str], list[str]] = {
    # ── Interfaces ────────────────────────────────────────────────────────────
    ("interface", "_interface", "missing_in_reality"): [
        "Interface is documented in NetBox but does not exist on the device — "
        "it may have been removed, renamed, or never provisioned.",
        "Interface name abbreviation mismatch — collector may be returning a "
        "short name (e.g. Et1) that does not match the canonical NetBox name.",
    ],
    ("interface", "_interface", "missing_in_intent"): [
        "Interface exists on the device but is not documented in NetBox — "
        "common for management interfaces, loopbacks, or sub-interfaces.",
        "Interface was added to the device without a corresponding NetBox update.",
    ],
    ("interface", "enabled", "value_mismatch"): [
        "Interface was manually shut or no-shut on the device without updating NetBox.",
        "A maintenance window left the interface in the wrong admin state.",
        "Automated provisioning changed the state on the device but not in NetBox.",
    ],
    ("interface", "description", "value_mismatch"): [
        "Description was updated in NetBox after a circuit change but not pushed to device.",
        "Description was changed directly on the device without updating NetBox.",
    ],
    ("interface", "ip_addresses", "value_mismatch"): [
        "IP address was changed directly on the device without updating NetBox.",
        "NetBox IP was reassigned to another device without reconfiguring this one.",
        "DHCP assigned a different address than the one documented in NetBox.",
    ],
    ("interface", "mode", "value_mismatch"): [
        "Switchport mode was changed on the device (access ↔ trunk) without updating NetBox.",
        "Device default mode differs from what NetBox expects for this port.",
    ],
    ("interface", "untagged_vlan", "value_mismatch"): [
        "Native or access VLAN was changed on the device without updating NetBox.",
        "VLAN renumbering on the device was not reflected in NetBox.",
    ],
    ("interface", "tagged_vlans", "value_mismatch"): [
        "VLANs were added or removed on the trunk port without updating NetBox.",
        "A VLAN provisioning script updated the device but not the NetBox record.",
    ],

    # ── VLANs ─────────────────────────────────────────────────────────────────
    ("vlan", "_vlan", "missing_in_reality"): [
        "VLAN is documented in NetBox but has not been provisioned on the device.",
        "VLAN was deleted from the device during cleanup without updating NetBox.",
    ],
    ("vlan", "_vlan", "missing_in_intent"): [
        "VLAN exists on the device but is not documented in NetBox — audit whether "
        "it is still needed or is a leftover from a previous deployment.",
        "VLAN was created directly on the device without a NetBox record.",
    ],
    ("vlan", "name", "value_mismatch"): [
        "VLAN name convention differs between NetBox and the device — usually cosmetic "
        "but can indicate the wrong VLAN is mapped.",
        "VLAN was renamed in NetBox after initial provisioning without updating the device.",
    ],

    # ── BGP neighbors ─────────────────────────────────────────────────────────
    ("bgp_neighbor", "_bgp_neighbor", "missing_in_reality"): [
        "BGP neighbor is documented in NetBox but is not configured on the device.",
        "Neighbor was removed from device config during a decommission without "
        "updating NetBox.",
    ],
    ("bgp_neighbor", "_bgp_neighbor", "missing_in_intent"): [
        "BGP neighbor is configured on the device but not documented in NetBox — "
        "verify this is an intentional peering.",
        "Neighbor was added to the device during troubleshooting and never cleaned up.",
    ],
    ("bgp_neighbor", "remote_as", "value_mismatch"): [
        "Wrong remote AS configured on the device — the session will not establish.",
        "Peer AS changed (e.g. after a provider migration) and NetBox was not updated.",
    ],
    ("bgp_neighbor", "enabled", "value_mismatch"): [
        "BGP neighbor was manually shut on the device without updating NetBox.",
        "Automated failover or maintenance script changed the neighbor state.",
    ],
    ("bgp_neighbor", "description", "value_mismatch"): [
        "BGP neighbor description was updated in NetBox after a peering change "
        "but not pushed to the device.",
        "Description was changed directly on the device without updating NetBox.",
    ],
    ("bgp_neighbor", "session_state", "value_mismatch"): [
        "BGP session is not in the expected state — check for routing policy issues, "
        "authentication mismatch, or reachability problems to the peer.",
        "Peer AS or address changed recently and the session has not re-established.",
        "Flapping link or MTU mismatch is preventing the session from staying up.",
    ],

    # ── OSPF adjacencies ──────────────────────────────────────────────────────
    ("ospf_adjacency", "_ospf_adjacency", "missing_in_reality"): [
        "OSPF adjacency is documented in NetBox but is not formed on the device — "
        "check area ID, authentication, hello/dead timers, and MTU.",
        "Neighbor was decommissioned but the NetBox record was not removed.",
        "Link between the two devices is down or OSPF is not enabled on the interface.",
    ],
    ("ospf_adjacency", "_ospf_adjacency", "missing_in_intent"): [
        "OSPF adjacency is present on the device but not documented in NetBox — "
        "verify this is an expected peering.",
        "Adjacency formed over a link that was not intended to run OSPF.",
    ],
    ("ospf_adjacency", "area", "value_mismatch"): [
        "Device and NetBox disagree on which OSPF area this adjacency belongs to — "
        "check area configuration on both ends of the link.",
    ],
    ("ospf_adjacency", "interface", "value_mismatch"): [
        "OSPF adjacency is forming over a different interface than documented — "
        "check for unexpected OSPF passive/active interface configuration.",
    ],
    ("ospf_adjacency", "adjacency_state", "value_mismatch"): [
        "OSPF adjacency is not in the expected state — check hello/dead timers, "
        "MTU, authentication, and network type on both ends.",
        "Adjacency is stuck in a non-Full state; common causes are OSPF area "
        "type mismatch or duplicate router IDs.",
    ],

    # ── Running config ────────────────────────────────────────────────────────
    ("config", "running_config", "value_mismatch"): [
        "Device running config differs from the NetBox-rendered intended config — "
        "the device may have been changed manually without updating NetBox templates.",
        "NetBox config template was updated but the new config has not been pushed "
        "to the device yet.",
        "Normalisation differences (whitespace, ordering) that the normaliser did "
        "not fully collapse — review the diff before treating as actionable.",
    ],
}


def diagnose(drift: dict) -> list[str]:
    object_type = drift["object"].split(":")[0]
    return _RULES.get((object_type, drift["field"], drift["drift_kind"]), [])
