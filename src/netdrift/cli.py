"""driftcheck — v0.1 CLI.

Wires the three v0.1 pieces together:
  netbox_client.get_intent()  -> intended state from NetBox
  collectors.arista.get_reality() -> real state from the device
  differ.diff()               -> structured drift records

Usage:
    driftcheck <device-name>
"""

import argparse
import sys
from pathlib import Path

import yaml

from netdrift import netbox_client, differ
from netdrift.collectors import arista, nokia

# devices.yml lives at the repo root, two levels up from this file
# (src/netdrift/cli.py -> src/netdrift -> src -> repo root).
DEVICES_FILE = Path(__file__).resolve().parents[2] / "devices.yml"

# Maps a normalized platform string (from NetBox intent, see schema.md
# Section 4) to the collector that handles that vendor. Adding a vendor =
# adding its collector here.
COLLECTORS = {
    "arista_eos": arista.get_reality,
    "nokia_srlinux": nokia.get_reality,
}

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


def main():
    parser = argparse.ArgumentParser(
        prog="driftcheck",
        description="Compare a device's intended state (NetBox) against its real state.",
    )
    parser.add_argument("device", help="device name (must match NetBox and devices.yml)")
    args = parser.parse_args()
    device_name = args.device

    devices = load_devices()
    if device_name not in devices:
        sys.exit(
            f"Error: '{device_name}' not in devices.yml. "
            f"Known devices: {', '.join(sorted(devices))}"
        )

    # The collector needs a dict with name + connection details.
    device = {"name": device_name, **devices[device_name]}

    try:
        intent = netbox_client.get_intent(device_name)
    except ValueError as e:
        sys.exit(f"Error fetching intent from NetBox: {e}")

    platform = intent["platform"]
    get_reality = COLLECTORS.get(platform)
    if get_reality is None:
        sys.exit(
            f"Error: no collector for platform '{platform}' (device "
            f"{device_name}). Known platforms: {', '.join(sorted(COLLECTORS))}"
        )

    try:
        reality = get_reality(device)
    except Exception as e:
        sys.exit(f"Error fetching reality from {device_name}: {e}")

    drift = differ.diff(intent, reality)
    print_drift(device_name, drift)


if __name__ == "__main__":
    main()