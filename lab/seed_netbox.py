"""
seed_netbox.py — Ticket 3, v0.1

Populates a fresh NetBox instance with the devices, interfaces, and IPs that
mirror the Containerlab topology in lab/topology.yml. Running this gives the
drift tool an "intended state" to compare live device state against.

Idempotent: safe to run repeatedly. Every object is get-or-created, so a
second run makes no changes and does not error.

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


# --- What we are seeding -----------------------------------------------------
# This mirrors lab/topology.yml. Interface names are CANONICAL (Ethernet1),
# not the Containerlab link name (eth1) — see docs/schema.md Rule 1.

DEVICES = [
    {
        "name": "core-sw-01",
        "interfaces": [
            {"name": "Ethernet1", "ip": "10.0.0.1/30"},
            {"name": "Management0", "ip": "172.20.20.11/24"},
        ],
    },
    {
        "name": "core-sw-02",
        "interfaces": [
            {"name": "Ethernet1", "ip": "10.0.0.2/30"},
            {"name": "Management0", "ip": "172.20.20.12/24"},
        ],
    },
]

MANUFACTURER = "Arista"
DEVICE_TYPE = "cEOS"          # model name
DEVICE_ROLE = "Core Switch"
SITE = "Lab"


def slugify(text):
    """NetBox requires a URL-safe 'slug' for many objects. Lowercase, dashes."""
    return text.lower().replace(" ", "-")


def get_or_create(endpoint, lookup, defaults):
    """
    Return an existing NetBox object matching `lookup`, or create it.

    endpoint : a pynetbox endpoint, e.g. nb.dcim.manufacturers
    lookup   : dict of fields used to find an existing object
    defaults : full dict of fields to use if we have to create it

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

    # 1. Manufacturer
    print("Manufacturer:")
    manufacturer = get_or_create(
        nb.dcim.manufacturers,
        lookup={"slug": slugify(MANUFACTURER)},
        defaults={"name": MANUFACTURER, "slug": slugify(MANUFACTURER)},
    )

    # 2. Device type (the model — belongs to the manufacturer)
    print("Device type:")
    device_type = get_or_create(
        nb.dcim.device_types,
        lookup={"slug": slugify(DEVICE_TYPE)},
        defaults={
            "manufacturer": manufacturer.id,
            "model": DEVICE_TYPE,
            "slug": slugify(DEVICE_TYPE),
        },
    )

    # 3. Device role
    print("Device role:")
    device_role = get_or_create(
        nb.dcim.device_roles,
        lookup={"slug": slugify(DEVICE_ROLE)},
        defaults={"name": DEVICE_ROLE, "slug": slugify(DEVICE_ROLE)},
    )

    # 4. Site
    print("Site:")
    site = get_or_create(
        nb.dcim.sites,
        lookup={"slug": slugify(SITE)},
        defaults={"name": SITE, "slug": slugify(SITE)},
    )

    # 5. Devices, their interfaces, and their IPs
    for entry in DEVICES:
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
                    "type": "1000base-t",   # interface physical type; arbitrary for a lab
                    "enabled": True,
                },
            )

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
            # Make sure the IP is attached to this interface even if it already
            # existed unassigned from a previous partial run.
            if ip.assigned_object_id != interface.id:
                ip.assigned_object_type = "dcim.interface"
                ip.assigned_object_id = interface.id
                ip.save()
                print(f"    (re-attached {ip} to {interface})")

    print("\nDone. NetBox now mirrors the Containerlab topology.")


if __name__ == "__main__":
    main()