"""
seed_netbox.py — Ticket 3, v0.1 (extended for v0.2 Nokia)

Populates a fresh NetBox instance with the devices, interfaces, IPs, and VLANs
that mirror the Containerlab topology in lab/topology.yml. Running this gives
the drift tool an "intended state" to compare live device state against.

Idempotent: safe to run repeatedly. Every object is get-or-created, so a
second run makes no changes and does not error.

v0.2: a Nokia SR Linux device is seeded in addition to the two Arista nodes.
Nokia is placed in its OWN site ("Lab-Nokia"), separate from the Arista site
("Lab"). NetBox VLANs are site-scoped, so this keeps each vendor's VLAN 10 a
distinct object — Arista's is named "users", Nokia's "mac-vrf-10" (matching
what collectors/nokia.py derives from the bound mac-vrf). netbox_client.py
already scopes VLANs by the device's site, so this is the schema-consistent
way to avoid a name collision on a shared VLAN id.

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


# --- Arista side (v0.1) ------------------------------------------------------
# Interface names are CANONICAL (Ethernet1), not the Containerlab link name
# (eth1) — see docs/schema.md Rule 1.

DEVICES = [
    {
        "name": "core-sw-01",
        "interfaces": [
            {"name": "Ethernet1", "ip": "10.0.0.1/30"},
            {"name": "Ethernet2", "mode": "access", "untagged_vlan": 10},
            {"name": "Ethernet3", "mode": "tagged", "tagged_vlans": [10, 20]},
            {"name": "Management0", "ip": "172.20.20.11/24"},
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
    },
]

# Arista VLANs. Site-scoped to the Arista site. Mirrors lab/configs/*.cfg.
VLANS = [
    {"vid": 1, "name": "default"},
    {"vid": 10, "name": "users"},
    {"vid": 20, "name": "voice"},
]

MANUFACTURER = "Arista"
DEVICE_TYPE = "cEOS"          # model name
DEVICE_ROLE = "Core Switch"
SITE = "Lab"

# --- Nokia side (v0.2) -------------------------------------------------------
# The Nokia node is seeded with only the interfaces that carry real config —
# ethernet-1/1 (the VLAN-10 access port) and mgmt0. The node has 59 physical
# interfaces; the rest are intentionally NOT seeded. Reality (nokia.py) will
# report all 59, so the differ flags the unseeded ones as missing_in_intent.
# That is correct, expected behaviour (schema Rule 9 — undocumented config IS
# drift), not a bug. It mirrors the Arista seeding, which also seeds only the
# configured interfaces, not every port.
#
# Interface names are SR Linux's canonical names ("ethernet-1/1", "mgmt0") —
# the names collectors/nokia.py reports — per schema Rule 1.

NOKIA_DEVICES = [
    {
        "name": "nokia-sw-01",
        "interfaces": [
            {"name": "ethernet-1/1", "mode": "access", "untagged_vlan": 10},
            {"name": "mgmt0", "ip": "172.20.20.21/24"},
        ],
    },
]

# Nokia VLANs, site-scoped to the Nokia site. VLAN 10's name is "mac-vrf-10"
# to match what collectors/nokia.py derives (the bound mac-vrf instance name);
# SR Linux has no native VLAN name.
NOKIA_VLANS = [
    {"vid": 10, "name": "mac-vrf-10"},
]

NOKIA_MANUFACTURER = "Nokia"
NOKIA_DEVICE_TYPE = "SR Linux"
NOKIA_SITE = "Lab-Nokia"


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


def seed_devices(nb, devices, device_type, device_role, site, vlan_objects):
    """Get-or-create each device, its interfaces, their VLAN config and IPs."""
    for entry in devices:
        print(f"Device {entry['name']}:")
        device = get_or_create(
            nb.dcim.devices,
            lookup={"name": entry["name"]},
            defaults={
                "name": entry["name"],
                "device_type": device_type.id,
                "role": device_role.id,
                "site": site.id,
            },
        )

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
                # Make sure the IP is attached even if it already existed
                # unassigned from a previous partial run.
                if ip.assigned_object_id != interface.id:
                    ip.assigned_object_type = "dcim.interface"
                    ip.assigned_object_id = interface.id
                    ip.save()
                    print(f"    (re-attached {ip} to {interface})")


def seed_vendor(nb, manufacturer_name, device_type_name, role,
                site_name, vlans, devices):
    """Seed one vendor: manufacturer, device-type, site, VLANs, devices.

    `role` is a NetBox device-role object, shared across vendors (created
    once in main()).
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

    print(f"Site {site_name}:")
    site = get_or_create(
        nb.dcim.sites,
        lookup={"slug": slugify(site_name)},
        defaults={"name": site_name, "slug": slugify(site_name)},
    )

    print("VLANs:")
    vlan_objects = seed_vlans(nb, site, vlans)

    seed_devices(nb, devices, device_type, role, site, vlan_objects)


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
        nb, MANUFACTURER, DEVICE_TYPE, device_role, SITE, VLANS, DEVICES
    )

    # Nokia vendor (its own site "Lab-Nokia").
    seed_vendor(
        nb, NOKIA_MANUFACTURER, NOKIA_DEVICE_TYPE, device_role,
        NOKIA_SITE, NOKIA_VLANS, NOKIA_DEVICES
    )

    print("\nDone. NetBox now mirrors the Containerlab topology.")


if __name__ == "__main__":
    main()