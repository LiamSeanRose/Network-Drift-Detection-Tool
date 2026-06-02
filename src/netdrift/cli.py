"""driftcheck — v0.1 CLI.

Wires the three v0.1 pieces together:
  netbox_client.get_intent()  -> intended state from NetBox
  collectors.arista.get_reality() -> real state from the device
  differ.diff()               -> structured drift records

Usage:
    driftcheck <device-name>      one-shot drift check against a live device
    driftcheck demo               drift on a bundled offline dataset (no setup)
"""

import argparse
import os
import sys
from pathlib import Path

import yaml

from netdrift import differ
from netdrift.collectors import registry
from netdrift.patterns.importer import import_patterns_dir
from netdrift.patterns.loader import PatternError, load_patterns_dir
from netdrift.pipeline import _resolve_intent_fn
from netdrift.storage.database import get_sessionmaker
from netdrift.storage.repository import create_api_key as _create_api_key_in_db
from netdrift.storage.repository import save_drifts

# devices.yml lives at the repo root, two levels up from this file
# (src/netdrift/cli.py -> src/netdrift -> src -> repo root).
REPO_ROOT = Path(__file__).resolve().parents[2]
DEVICES_FILE = REPO_ROOT / "devices.yml"

# Bundled community patterns live at the repo root, imported by default.
PATTERNS_DIR = REPO_ROOT / "patterns"

# Single source of truth for vendor dispatch: the collector registry, shared
# with pipeline.py. Adding a vendor is a new self-registering collector module
# (collectors/base.py) — no edit here.
COLLECTORS = registry.build_collectors()


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


def _seed_demo(session_factory=None):
    """Persist the bundled demo drift into the database so the dashboard shows it.

    Opt-in (`--seed`). Unlike the terminal demo, this needs DATABASE_URL set and
    the schema migrated (`alembic upgrade head`). Tests inject `session_factory`.
    """
    from netdrift.demo import demo_drift_records

    if session_factory is None:
        try:
            session_factory = get_sessionmaker()
        except RuntimeError as e:
            sys.exit(
                f"Error: {e}\n"
                "Set DATABASE_URL (and run 'alembic upgrade head') to seed the "
                "demo into a database."
            )

    records = demo_drift_records()
    try:
        with session_factory() as session:
            save_drifts(session, records)
            session.commit()
    except Exception as e:
        sys.exit(f"Error seeding demo drift: {e}")

    print(f"\nSeeded {len(records)} demo drift record(s) into the database.")
    print("Start the API and dashboard to see them:")
    print("  uvicorn netdrift.api.app:app --port 8001")
    print("  cd frontend && npm install && npm run dev   # http://localhost:5173")


def _cmd_demo(argv):
    """Run drift detection on a bundled offline dataset — no NetBox, device, or DB.

    This is the zero-setup first-run experience: `driftcheck demo` shows real
    differ output on a fictional two-device network so a brand-new user sees what
    drift looks like before wiring up NetBox and the lab. With `--seed` it also
    persists that drift so the React dashboard lights up.
    """
    from netdrift.demo import demo_pairs

    parser = argparse.ArgumentParser(
        prog="driftcheck demo",
        description=(
            "Show drift on a bundled offline dataset. No NetBox, no live "
            "device, no database required — the perfect first run."
        ),
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="also persist the demo drift to the database so the dashboard "
             "shows it (needs DATABASE_URL + 'alembic upgrade head')",
    )
    args = parser.parse_args(argv)

    pairs = demo_pairs()
    print(
        "netdrift demo — drift on a bundled fictional network "
        "(no NetBox / device / database).\n"
        f"{len(pairs)} device(s); this is real differ output.\n"
    )

    total = 0
    for device_name, intent, reality in pairs:
        drift = differ.diff(intent, reality)
        total += len(drift)
        print_drift(device_name, drift)

    print(f"Demo complete: {total} drift record(s) across {len(pairs)} device(s).")

    if args.seed:
        _seed_demo()
    else:
        print(
            "Next: copy devices.example.yml to devices.yml and run "
            "'driftcheck <device>' against your own network,\n"
            "or run 'driftcheck demo --seed' to populate the dashboard."
        )


def _cmd_create_api_key(argv, session_factory=None):
    """Bootstrap a new API key and print it once to stdout."""
    parser = argparse.ArgumentParser(
        prog="driftcheck create-api-key",
        description=(
            "Create an API key and print it once. "
            "Store it somewhere safe — it is never shown again."
        ),
    )
    parser.add_argument("--name", required=True, help="human label for this key (e.g. 'admin')")
    args = parser.parse_args(argv)

    if session_factory is None:
        try:
            session_factory = get_sessionmaker()
        except RuntimeError as e:
            sys.exit(f"Error: {e}")

    try:
        with session_factory() as session:
            raw_key, _ = _create_api_key_in_db(session, args.name)
            session.commit()
    except Exception as e:
        sys.exit(f"Error creating API key: {e}")

    print(raw_key)


def _cmd_import_patterns(argv, session_factory=None):
    """Load community pattern YAML files and upsert them into known_issues."""
    parser = argparse.ArgumentParser(
        prog="driftcheck import-patterns",
        description=(
            "Load community pattern YAML files and upsert them into the "
            "known_issues table. Idempotent — safe to re-run."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(PATTERNS_DIR),
        help="directory of .yaml pattern files (default: ./patterns)",
    )
    args = parser.parse_args(argv)

    if session_factory is None:
        try:
            session_factory = get_sessionmaker()
        except RuntimeError as e:
            sys.exit(f"Error: {e}")

    try:
        with session_factory() as session:
            summary = import_patterns_dir(session, args.path)
            session.commit()
    except PatternError as e:
        sys.exit(f"Error importing patterns: {e}")

    print(
        f"Imported {summary['files']} pattern file(s): "
        f"{summary['created']} created, {summary['updated']} updated "
        f"({len(summary['fingerprints'])} fingerprint(s) total)."
    )


def _cmd_validate_patterns(argv):
    """Validate pattern YAML files without touching the database.

    Loads and schema-validates every pattern and checks for fingerprint
    collisions. Exits non-zero on any problem — this is what the CI
    `validate-patterns` job runs, so adding a valid pattern needs no Python
    change and a malformed one fails the build.
    """
    parser = argparse.ArgumentParser(
        prog="driftcheck validate-patterns",
        description="Validate community pattern YAML files (no database needed).",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(PATTERNS_DIR),
        help="directory of .yaml pattern files (default: ./patterns)",
    )
    args = parser.parse_args(argv)

    try:
        loaded = load_patterns_dir(args.path)
    except PatternError as e:
        sys.exit(f"Pattern validation failed: {e}")

    fingerprints = sum(len(p.drift_kinds) for _, p in loaded)
    print(
        f"Validated {len(loaded)} pattern file(s) "
        f"({fingerprints} fingerprint(s)). All valid."
    )


def main(argv=None, collectors=None):
    """Run a one-shot drift check for one device.

    `collectors` defaults to the registry-backed COLLECTORS table; tests inject
    a fake dict. `argv` defaults to sys.argv; tests pass an explicit list.
    """
    # Materialize argv so we can inspect it before the drift-check parser runs.
    if argv is None:
        argv = sys.argv[1:]

    if argv and argv[0] == "demo":
        _cmd_demo(argv[1:])
        return

    if argv and argv[0] == "create-api-key":
        _cmd_create_api_key(argv[1:])
        return

    if argv and argv[0] == "import-patterns":
        _cmd_import_patterns(argv[1:])
        return

    if argv and argv[0] == "validate-patterns":
        _cmd_validate_patterns(argv[1:])
        return

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

    try:
        get_intent = _resolve_intent_fn()
    except ValueError as e:
        sys.exit(f"Error: {e}")
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