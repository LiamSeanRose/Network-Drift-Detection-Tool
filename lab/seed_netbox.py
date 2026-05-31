"""
seed_netbox.py — Ticket 3, v0.1 (extended for v0.2 Nokia, v0.3 routing intent)

Populates a fresh NetBox instance with the devices, interfaces, IPs, VLANs,
and platforms that mirror the Containerlab topology in lab/topology.yml.
Running this gives the drift tool an "intended state" to compare live device
state against.

Idempotent: safe to run repeatedly. Every object is get-or-created.

v0.2: a Nokia SR Linux device is seeded alongside the two Arista nodes, in its
own site ("Lab-Nokia"). Each device is assigned a NetBox platform object whose
slug netbox_client.py maps to a schema platform string — this is how the CLI
knows which collector to dispatch to.

v0.3: per-device routing intent (BGP neighbors + OSPF adjacencies) is written
into each device's `local_context_data` — NetBox has no native BGP/OSPF model,
so routing intent lives in the config context. The JSON shape mirrors the
device-state schema exactly (docs/schema.md Section 2) so the differ can
compare intent and reality with no translation. The two Arista nodes get
real iBGP + OSPF intent matching what the lab actually runs (see
lab/configs/core-sw-0[12].cfg). The Nokia node has no routing configured in
the lab today, so its intent is also empty — when routing is later added
to the Nokia, fill in its bgp_neighbors / ospf_adjacencies lists below.

Requires two environment variables:
    NETBOX_URL    e.g. http://localhost:8000
    NETBOX_TOKEN  a NetBox API token with write access

Usage:
    export NETBOX_URL=http://localhost:8000
    export NETBOX_TOKEN=<your token>
    python lab/seed_netbox.py
"""

import os
import sys

import pynetbox


# --- Arista side (v0.1 + v0.3 routing) --------------------------------------
# Interface names are CANONICAL (Ethernet1), not the Containerlab link name
# (eth1) — see docs/schema.md Rule 1.
#
# bgp_neighbors / ospf_adjacencies are new in v0.3 — they get written into
# each device's local_context_data, where netbox_client._build_routing_from_
# context() reads them back. Shape mirrors collectors/arista.py output.
#
# session_state and adjacency_state are declared as "established" / "full"
# (the up states). Per schema Section 10 Q1, operational state IS drift —
# meaning the operator declares "I expect this peer up", and a peer that
# isn't established becomes a warning. Consistent with the ratified
# decision.

DEVICES = [
    {
        "name": "core-sw-01",
        "interfaces": [
            {"name": "Ethernet1", "ip": "10.0.0.1/30"},
            {"name": "Ethernet2", "mode": "access", "untagged_vlan": 10},
            {"name": "Ethernet3", "mode": "tagged", "tagged_vlans": [10, 20]},
            {"name": "Management0", "ip": "172.20.20.11/24"},
        ],
        "bgp_neighbors": [
            {
                "peer": "10.0.0.2",
                "remote_as": 65000,
                "enabled": True,
                "description": "iBGP to core-sw-02",
                "session_state": "established",
            },
        ],
        "ospf_adjacencies": [
            {
                "router_id": "2.2.2.2",
                "area": "0.0.0.0",
                "interface": "Ethernet1",
                "adjacency_state": "full",
            },
        ],
    },
    {
        "name": "core-sw-02",
        "interfaces": [
            {"name": "Ethernet1", "ip": "10.0.0.2/30"},
            {"name": "Ethernet2", "mode": "access", "untagged_vlan": 10},
            {"name": "Ethernet3", "mode": "tagged", "tagged_vlans": [10, 20]},
            {"name": "Management0", "ip": "172.20.20.12/24"},
        ],
        "bgp_neighbors": [
            {
                "peer": "10.0.0.1",
                "remote_as": 65000,
                "enabled": True,
                "description": "iBGP to core-sw-01",
                "session_state": "established",
            },
        ],
        "ospf_adjacencies": [
            {
                "router_id": "1.1.1.1",
                "area": "0.0.0.0",
                "interface": "Ethernet1",
                "adjacency_state": "full",
            },
        ],
    },
]

VLANS = [
    {"vid": 1, "name": "default"},
    {"vid": 10, "name": "users"},
    {"vid": 20, "name": "voice"},
]

MANUFACTURER = "Arista"
DEVICE_TYPE = "cEOS"          # model name
DEVICE_ROLE = "Core Switch"
PLATFORM = "Arista EOS"       # NetBox platform; slug "arista-eos"
SITE = "Lab"

# --- Nokia side (v0.2) -------------------------------------------------------
# Only the configured interfaces are seeded — ethernet-1/1 (the VLAN-10 access
# port) and mgmt0. The node has 59 physical interfaces; the rest are
# intentionally NOT seeded. Reality (nokia.py) reports all 59, so the differ
# flags the unseeded ones as missing_in_intent — correct, expected behaviour
# (schema Rule 9), not a bug. Mirrors the Arista seeding, which also seeds only
# the configured interfaces.
#
# Interface names are SR Linux's canonical names ("ethernet-1/1", "mgmt0") —
# the names collectors/nokia.py reports — per schema Rule 1.
#
# v0.3: bgp_neighbors and ospf_adjacencies are EMPTY because the Nokia in the
# lab has no routing configured today. Reality also reports empty -> no drift,
# which is correct. When the lab grows the Nokia into the routing fabric,
# populate these lists in the same shape as the Arista entries above.

NOKIA_DEVICES = [
    {
        "name": "nokia-sw-01",
        "interfaces": [
            {"name": "ethernet-1/1", "mode": "access", "untagged_vlan": 10},
            {"name": "mgmt0", "ip": "172.20.20.21/24"},
        ],
        "bgp_neighbors": [],
        "ospf_adjacencies": [],
    },
]

# VLAN 10's name is "mac-vrf-10" to match what collectors/nokia.py derives
# (the bound mac-vrf instance name); SR Linux has no native VLAN name.
NOKIA_VLANS = [
    {"vid": 10, "name": "mac-vrf-10"},
]

NOKIA_MANUFACTURER = "Nokia"
NOKIA_DEVICE_TYPE = "SR Linux"
NOKIA_PLATFORM = "Nokia SRLinux"   # NetBox platform; slug "nokia-srlinux"
NOKIA_SITE = "Lab-Nokia"

# --- Cisco side (v0.3) -------------------------------------------------------
# Physical Catalyst 3850 at 192.168.5.50. GigabitEthernet0/0 is the dedicated
# management port at the back of the chassis; this is the only interface
# configured in the lab today. No VLANs, BGP, or OSPF seeded yet — the switch
# is newly added and has no routing/L2 config beyond management access.
# When lab configs are pushed to the 3850 (e.g. access ports, routing), add the
# interfaces and routing intent here in the same shape as the Arista entries.

CISCO_DEVICES = [
    {
        "name": "cisco-sw-01",
        "interfaces": [
            {"name": "GigabitEthernet0/0", "ip": "192.168.5.50/24"},
        ],
        "bgp_neighbors": [],
        "ospf_adjacencies": [],
    },
]

CISCO_VLANS = []   # no VLANs configured on the 3850 yet

CISCO_MANUFACTURER = "Cisco"
CISCO_DEVICE_TYPE = "Catalyst 3850"
CISCO_PLATFORM = "Cisco IOS-XE"   # NetBox platform; slug "cisco-ios-xe"
CISCO_SITE = "Lab-Cisco"


def slugify(text):
    """NetBox requires a URL-safe 'slug' for many objects. Lowercase, dashes."""
    return text.lower().replace(" ", "-")


def get_or_create(endpoint, lookup, defaults):
    """
    Return an existing NetBox object matching `lookup`, or create it.

    This is what makes the script idempotent: a second run finds everything
    already there and creates nothing.
    """
    existing = endpoint.get(**lookup)
    if existing:
        print(f"  exists:  {existing}")
        return existing
    created = endpoint.create(defaults)
    print(f"  created: {created}")
    return created


def seed_vlans(nb, site, vlans):
    """Get-or-create the given VLANs, scoped to `site`.

    Returns {vid: NetBox VLAN object} for use in interface assignment.
    """
    vlan_objects = {}
    for vlan in vlans:
        vlan_obj = get_or_create(
            nb.ipam.vlans,
            lookup={"vid": vlan["vid"], "site_id": site.id},
            defaults={
                "vid": vlan["vid"],
                "name": vlan["name"],
                "site": site.id,
            },
        )
        vlan_objects[vlan["vid"]] = vlan_obj
    return vlan_objects


def _build_routing_context(entry):
    """Build the local_context_data JSON for a device's routing intent.

    Shape mirrors the device-state schema (docs/schema.md Section 2) so the
    differ can compare intent and reality without translation. Empty lists
    in the seed produce empty dicts in the context (schema Rule 4 spirit:
    no None).
    """
    bgp = {}
    for peer in entry.get("bgp_neighbors", []):
        bgp[peer["peer"]] = {
            "remote_as": peer["remote_as"],
            "enabled": peer.get("enabled", True),
            "description": peer.get("description", ""),
            "session_state": peer.get("session_state", "established"),
        }

    adjacencies = {}
    for adj in entry.get("ospf_adjacencies", []):
        adjacencies[adj["router_id"]] = {
            "area": adj["area"],
            "interface": adj["interface"],
            "adjacency_state": adj.get("adjacency_state", "full"),
        }

    return {
        "bgp_neighbors": bgp,
        "ospf": {"adjacencies": adjacencies},
    }


def seed_routing_intent(device, entry):
    """PATCH the device's local_context_data with the routing intent.

    Idempotent: if the existing context already matches, no PATCH is sent.
    NetBox merges all applicable config contexts on read, but local_context_
    data is the per-device override and is what we own here.
    """
    payload = _build_routing_context(entry)
    if device.local_context_data == payload:
        print("  routing context: unchanged")
        return
    device.local_context_data = payload
    device.save()
    n_bgp = len(payload["bgp_neighbors"])
    n_ospf = len(payload["ospf"]["adjacencies"])
    print(f"  routing context: {n_bgp} BGP peer(s), {n_ospf} OSPF adjacency(ies)")


def seed_devices(nb, devices, device_type, platform, role, site, vlan_objects,
                 config_template=None):
    """Get-or-create each device, its interfaces, their VLAN config and IPs.

    `config_template`, when given, is assigned to each device so render-config
    has a template to render (v1.0 config drift).
    """
    for entry in devices:
        print(f"Device {entry['name']}:")
        device = get_or_create(
            nb.dcim.devices,
            lookup={"name": entry["name"]},
            defaults={
                "name": entry["name"],
                "device_type": device_type.id,
                "role": role.id,
                "platform": platform.id,
                "site": site.id,
            },
        )

        # get_or_create only applies `defaults` on creation. A device that
        # already existed from an earlier run keeps its old platform (or
        # none). Reconcile it explicitly, same as the IP re-attach below.
        if device.platform is None or device.platform.id != platform.id:
            device.platform = platform.id
            device.save()
            print(f"  set platform={platform.name}")

        # Assign the Config Template so render-config has something to render.
        # Reconciled explicitly for the same reason as platform: get_or_create
        # only sets fields on creation, so a pre-existing device needs the
        # assignment applied here.
        if config_template is not None and (
            device.config_template is None
            or device.config_template.id != config_template.id
        ):
            device.config_template = config_template.id
            device.save()
            print(f"  set config_template={config_template.name}")

        for iface in entry["interfaces"]:
            print(f"  Interface {iface['name']}:")
            interface = get_or_create(
                nb.dcim.interfaces,
                lookup={"device_id": device.id, "name": iface["name"]},
                defaults={
                    "device": device.id,
                    "name": iface["name"],
                    "type": "1000base-t",   # physical type; arbitrary for a lab
                    "enabled": True,
                },
            )
            # VLAN config, if any. Routed interfaces have no `mode` key and
            # are left untouched (NetBox mode stays empty, which
            # netbox_client.py reads as "routed").
            mode = iface.get("mode")
            if mode:
                interface.mode = mode
                if iface.get("untagged_vlan") is not None:
                    interface.untagged_vlan = vlan_objects[
                        iface["untagged_vlan"]
                    ].id
                if iface.get("tagged_vlans"):
                    interface.tagged_vlans = [
                        vlan_objects[v].id for v in iface["tagged_vlans"]
                    ]
                interface.save()
                print(f"    set mode={mode}")

            # IP assignment — only for interfaces that carry one.
            if iface.get("ip"):
                print(f"  IP {iface['ip']}:")
                ip = get_or_create(
                    nb.ipam.ip_addresses,
                    lookup={"address": iface["ip"]},
                    defaults={
                        "address": iface["ip"],
                        "assigned_object_type": "dcim.interface",
                        "assigned_object_id": interface.id,
                    },
                )
                if ip.assigned_object_id != interface.id:
                    ip.assigned_object_type = "dcim.interface"
                    ip.assigned_object_id = interface.id
                    ip.save()
                    print(f"    (re-attached {ip} to {interface})")

        # v0.3: routing intent into local_context_data, after interfaces are
        # in place so the device object is fully realized.
        seed_routing_intent(device, entry)


# --- v1.0 config-level drift: NetBox Config Templates ----------------------
# render-config (dcim/devices/{id}/render-config/) renders the device's
# assigned Config Template; netbox_client._fetch_rendered_config() returns that
# text as the intent-side `running_config`. With no template assigned it returns
# "", so config drift never fires. These templates give each device an intended
# config to compare against `show running-config`.
#
# STARTER TEMPLATES — deliberately minimal. A NetBox-rendered template will not
# match a full device running-config out of the box (the "semantic-equivalence
# problem", schema.md Section 9), so expect the first lab run to show config
# drift covering everything the device emits that these templates don't. Expand
# them against real `show running-config` output, or scope the compared section,
# once you can iterate on the lab. Nokia is intentionally omitted: its collector
# returns running_config "" (SR Linux has no matching text config), so the
# differ skips it regardless of intent.
#
# Context note: NetBox renders these with the device ORM object in scope, so
# `device.interfaces.all()` and `interface.ip_addresses.all()` are available.

ARISTA_CONFIG_TEMPLATE = """\
hostname {{ device.name }}
!
{% for interface in device.interfaces.all() %}
interface {{ interface.name }}
{%- if interface.description %}
   description {{ interface.description }}
{%- endif %}
{%- for ip in interface.ip_addresses.all() %}
   ip address {{ ip.address }}
{%- endfor %}
!
{% endfor %}
end
"""

# IOS-XE uses a dotted-decimal mask (not CIDR), so IP lines are left out of this
# starter to avoid emitting Arista-style addresses; descriptions are enough to
# prove the path. Add IPs with proper mask conversion when tuning on the lab.
CISCO_CONFIG_TEMPLATE = """\
hostname {{ device.name }}
!
{% for interface in device.interfaces.all() %}
interface {{ interface.name }}
{%- if interface.description %}
 description {{ interface.description }}
{%- endif %}
!
{% endfor %}
end
"""


def seed_config_template(nb, name, template_code):
    """Get-or-create a NetBox Config Template, reconciling its body on re-runs.

    Unlike most seeded objects, the template body is expected to change as it is
    tuned against real device output, so an existing template's template_code is
    updated when it differs (get_or_create alone only sets fields on creation).
    """
    print(f"Config template {name}:")
    template = get_or_create(
        nb.extras.config_templates,
        lookup={"name": name},
        defaults={"name": name, "template_code": template_code},
    )
    if template.template_code != template_code:
        template.template_code = template_code
        template.save()
        print("  updated: template_code")
    return template


def seed_vendor(nb, manufacturer_name, device_type_name, platform_name, role,
                site_name, vlans, devices, config_template_code=None):
    """Seed one vendor: manufacturer, device-type, platform, site, VLANs,
    devices. `role` is a NetBox device-role object, shared across vendors.

    `config_template_code` is optional Jinja2 for a per-vendor Config Template
    assigned to each of that vendor's devices (v1.0 config drift). When None
    (e.g. Nokia), no template is created or assigned.
    """
    print(f"Manufacturer {manufacturer_name}:")
    manufacturer = get_or_create(
        nb.dcim.manufacturers,
        lookup={"slug": slugify(manufacturer_name)},
        defaults={"name": manufacturer_name, "slug": slugify(manufacturer_name)},
    )

    print(f"Device type {device_type_name}:")
    device_type = get_or_create(
        nb.dcim.device_types,
        lookup={"slug": slugify(device_type_name)},
        defaults={
            "manufacturer": manufacturer.id,
            "model": device_type_name,
            "slug": slugify(device_type_name),
        },
    )

    print(f"Platform {platform_name}:")
    platform = get_or_create(
        nb.dcim.platforms,
        lookup={"slug": slugify(platform_name)},
        defaults={"name": platform_name, "slug": slugify(platform_name)},
    )

    print(f"Site {site_name}:")
    site = get_or_create(
        nb.dcim.sites,
        lookup={"slug": slugify(site_name)},
        defaults={"name": site_name, "slug": slugify(site_name)},
    )

    print("VLANs:")
    vlan_objects = seed_vlans(nb, site, vlans)

    config_template = None
    if config_template_code:
        config_template = seed_config_template(
            nb, f"{platform_name} drift baseline", config_template_code
        )

    seed_devices(nb, devices, device_type, platform, role, site, vlan_objects,
                 config_template)


def main():
    url = os.environ.get("NETBOX_URL")
    token = os.environ.get("NETBOX_TOKEN")
    if not url or not token:
        sys.exit(
            "ERROR: set NETBOX_URL and NETBOX_TOKEN environment variables first.\n"
            "  export NETBOX_URL=http://localhost:8000\n"
            "  export NETBOX_TOKEN=<your token>"
        )

    nb = pynetbox.api(url, token=token)

    # Device role is shared across vendors — create it once.
    print("Device role:")
    device_role = get_or_create(
        nb.dcim.device_roles,
        lookup={"slug": slugify(DEVICE_ROLE)},
        defaults={"name": DEVICE_ROLE, "slug": slugify(DEVICE_ROLE)},
    )

    # Arista vendor (site "Lab").
    seed_vendor(
        nb, MANUFACTURER, DEVICE_TYPE, PLATFORM, device_role,
        SITE, VLANS, DEVICES, config_template_code=ARISTA_CONFIG_TEMPLATE
    )

    # Nokia vendor (its own site "Lab-Nokia"). No config template: the Nokia
    # collector returns running_config "" so config drift is skipped anyway.
    seed_vendor(
        nb, NOKIA_MANUFACTURER, NOKIA_DEVICE_TYPE, NOKIA_PLATFORM, device_role,
        NOKIA_SITE, NOKIA_VLANS, NOKIA_DEVICES
    )

    # Cisco vendor (its own site "Lab-Cisco").
    seed_vendor(
        nb, CISCO_MANUFACTURER, CISCO_DEVICE_TYPE, CISCO_PLATFORM, device_role,
        CISCO_SITE, CISCO_VLANS, CISCO_DEVICES,
        config_template_code=CISCO_CONFIG_TEMPLATE
    )

    print("\nDone. NetBox now mirrors the Containerlab topology.")


if __name__ == "__main__":
    main()