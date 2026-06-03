"""inventory.py — resolve the device inventory (the list + credentials).

The device LIST can come from two sources:
  - "yaml"   (default): the local devices.yml, as today.
  - "netbox" (opt-in via DEVICE_SOURCE=netbox): NetBox as the source of truth
    for what exists — the model a NetBox shop expects, where you don't hand-
    maintain a parallel device list.

CREDENTIALS never come from NetBox (it stores none). Under NetBox sourcing they
come from one service account in the environment — NETDRIFT_DEVICE_USERNAME /
NETDRIFT_DEVICE_PASSWORD — which maps onto the common deployment of a single
read-only TACACS+/RADIUS account for the whole fleet. devices.yml, if present,
still provides optional per-device overrides (a different host, a one-off
credential, a reachability_port), so a handful of exceptions don't force you
back to maintaining the whole list by hand.

The default path (DEVICE_SOURCE unset → "yaml") is byte-for-byte the old
behaviour: this is purely additive.
"""

import os

import yaml

from netdrift import netbox_client
from netdrift.cli import DEVICES_FILE, load_devices


def _service_account_credentials():
    """The fleet-wide service-account credentials from the environment.

    Either may be None — a collector that needs credentials will then fail
    loudly for that device, which is the right signal that the service account
    was not configured, rather than silently probing with no auth.
    """
    return {
        "username": os.environ.get("NETDRIFT_DEVICE_USERNAME"),
        "password": os.environ.get("NETDRIFT_DEVICE_PASSWORD"),
    }


def _yaml_overrides(path=DEVICES_FILE):
    """Load devices.yml as optional per-device overrides; {} if it is absent.

    Unlike cli.load_devices this never exits — under NetBox sourcing the file is
    optional, holding only exceptions to the service-account default.
    """
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def resolve_inventory(*, source=None, netbox_lister=None,
                      yaml_loader=None, overrides_loader=None):
    """Return ``{name: {hostname, username, password, ...}}`` — the same shape
    cli.load_devices returns, so callers (scheduler) are source-agnostic.

    Args:
        source: "yaml" or "netbox". Defaults to the DEVICE_SOURCE env var, then
            "yaml".
        netbox_lister: callable() -> [{name, hostname}]. Defaults to
            netbox_client.list_devices; tests inject a fake (no live NetBox).
        yaml_loader: callable() -> dict, for the yaml source. Defaults to
            cli.load_devices.
        overrides_loader: callable() -> dict of per-device overrides for the
            netbox source. Defaults to reading devices.yml if present.

    Raises ValueError on an unknown source.
    """
    source = (source or os.environ.get("DEVICE_SOURCE", "yaml")).lower()

    if source == "yaml":
        return (yaml_loader or load_devices)()

    if source == "netbox":
        lister = netbox_lister or netbox_client.list_devices
        overrides = (overrides_loader or _yaml_overrides)()
        creds = _service_account_credentials()
        inventory = {}
        for entry in lister():
            name = entry["name"]
            override = overrides.get(name) or {}
            details = {
                "hostname": override.get("hostname") or entry.get("hostname") or name,
                "username": override.get("username") or creds["username"],
                "password": override.get("password") or creds["password"],
            }
            # Carry through any extra override keys (reachability_port, etc.)
            # without letting them clobber the three resolved above.
            for key, value in override.items():
                details.setdefault(key, value)
            inventory[name] = details
        return inventory

    raise ValueError(
        f"Unknown DEVICE_SOURCE '{source}'. Valid values: 'yaml', 'netbox'."
    )
