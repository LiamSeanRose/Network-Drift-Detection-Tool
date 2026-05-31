"""driftcheck — v0.1 CLI.

Wires the three v0.1 pieces together:
  netbox_client.get_intent()  -> intended state from NetBox
  collectors.arista.get_reality() -> real state from the device
  differ.diff()               -> structured drift records

Usage:
    driftcheck <device-name>
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

from netdrift import netbox_client, differ
from netdrift.collectors import registry

# devices.yml lives at the repo root, two levels up from this file
# (src/netdrift/cli.py -> src/netdrift -> src -> repo root).
DEVICES_FILE = Path(__file__).resolve().parents[2] / "devices.yml"

# Single source of truth for vendor dispatch: the collector registry, shared
# with pipeline.py. Adding a vendor is a new self-registering collector module
# (collectors/base.py) — no edit here.
COLLECTORS = registry.build_collectors()

def _resolve_intent_fn():
    """Return the get_intent callable for the configured source of truth.

    Reads SOURCE_OF_TRUTH from the environment. Defaults to 'netbox'.
    """
    source = os.environ.get("SOURCE_OF_TRUTH", "netbox").lower()
    if source == "nautobot":
        from netdrift import nautobot_client
        return nautobot_client.get_intent
    if source == "netbox":
        return netbox_client.get_intent
    sys.exit(
        f"Error: unknown SOURCE_OF_TRUTH '{source}'. "
        f"Valid values: 'netbox', 'nautobot'."
    )


def load_devices(path=DEVICES_FILE):
    """Load the device inventory (connection details + credentials)."""
    if not path.exists():
        sys.exit(
            f"Error: {path.name} not found at {path}.\n"
            f"Copy devices.example.yml to devices.yml and fill in real values."
        )
    with open(path) as f:
        devices = yaml.safe_load(f)
    if not devices:
        sys.exit(f"Error: {path.name} is empty.")
    return devices


def print_drift(device_name, drift):
    """Pretty-print the list of drift records to the terminal."""
    if not drift:
        print(f"OK  {device_name}: no drift — intent and reality match.")
        return

    print(f"DRIFT  {device_name}: {len(drift)} difference(s) found.\n")
    for record in drift:
        print(f"  [{record['severity'].upper()}] {record['object']} / {record['field']}")
        print(f"      intent:  {record['intent']}")
        print(f"      reality: {record['reality']}")
        print(f"      kind:    {record['drift_kind']}")
        print()


def main(argv=None, collectors=None):
    """Run a one-shot drift check for one device.

    `collectors` defaults to the registry-backed COLLECTORS table; tests inject
    a fake dict. `argv` defaults to sys.argv; tests pass an explicit list.
    """
    if collectors is None:
        collectors = COLLECTORS

    parser = argparse.ArgumentParser(
        prog="driftcheck",
        description="Compare a device's intended state (NetBox) against its real state.",
    )
    parser.add_argument("device", help="device name (must match NetBox and devices.yml)")
    args = parser.parse_args(argv)
    device_name = args.device

    devices = load_devices()
    if device_name not in devices:
        sys.exit(
            f"Error: '{device_name}' not in devices.yml. "
            f"Known devices: {', '.join(sorted(devices))}"
        )

    # The collector needs a dict with name + connection details.
    device = {"name": device_name, **devices[device_name]}

    get_intent = _resolve_intent_fn()
    source_label = os.environ.get("SOURCE_OF_TRUTH", "netbox").capitalize()
    try:
        intent = get_intent(device_name)
    except ValueError as e:
        sys.exit(f"Error fetching intent from {source_label}: {e}")

    platform = intent["platform"]
    get_reality = collectors.get(platform)
    if get_reality is None:
        sys.exit(
            f"Error: no collector for platform '{platform}' (device "
            f"{device_name}). Known platforms: {', '.join(sorted(collectors))}"
        )

    try:
        reality = get_reality(device)
    except Exception as e:
        sys.exit(f"Error fetching reality from {device_name}: {e}")

    drift = differ.diff(intent, reality)
    print_drift(device_name, drift)


if __name__ == "__main__":
    main()