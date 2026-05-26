"""pipeline.py — the reusable drift-check pipeline (v0.2).

One function, run_drift_check(device), runs the full loop for a single device:
    get_intent (NetBox)  ->  get_reality (collector)  ->  diff  ->  save

It is the non-interactive core of cli.py: the same wiring, but it takes a
device dict, returns the drift records, and persists them — no argument
parsing, no printing. The scheduler calls this on a timer; v0.3's syslog
trigger will call it directly too. Keeping it separate from any caller is
what makes both the CLI path and the scheduled path share one tested core.

The collector functions and netbox_client talk to the live lab, so a true
end-to-end run needs the lab. The wiring itself is tested in
tests/test_pipeline.py by injecting fake intent/reality/save callables, so
the orchestration is verified without any device or database.

Public function:
    run_drift_check(device, *, get_intent=..., collectors=..., session_factory=...)
        -> list[drift record]
"""

from netdrift import differ, netbox_client
from netdrift.collectors import arista, nokia
from netdrift.storage.database import get_sessionmaker
from netdrift.storage.repository import save_drifts

# Maps a normalized platform string (from intent) to the collector that
# handles that vendor — mirrors the dispatch in cli.py. Adding a vendor =
# adding its collector here.
COLLECTORS = {
    "arista_eos": arista.get_reality,
    "nokia_srlinux": nokia.get_reality,
}


def run_drift_check(device, *, get_intent=netbox_client.get_intent,
                    collectors=None, session_factory=None):
    """Run the full drift pipeline for one device and persist the result.

    Args:
        device: dict with at least "name" plus the connection details the
            collector needs (hostname, username, password).
        get_intent: callable(device_name) -> intent dict. Defaults to the real
            NetBox client; tests inject a fake.
        collectors: dict {platform: get_reality_callable}. Defaults to the real
            COLLECTORS table; tests inject fakes.
        session_factory: callable() -> Session context manager. Defaults to the
            real sessionmaker; tests inject an in-memory one.

    Returns the list of drift records produced by differ.diff (also persisted).

    Raises:
        ValueError if the device's platform has no registered collector.
    """
    if collectors is None:
        collectors = COLLECTORS
    if session_factory is None:
        session_factory = get_sessionmaker()

    device_name = device["name"]

    intent = get_intent(device_name)

    platform = intent["platform"]
    get_reality = collectors.get(platform)
    if get_reality is None:
        raise ValueError(
            f"No collector for platform '{platform}' (device {device_name}). "
            f"Known platforms: {', '.join(sorted(collectors))}"
        )

    reality = get_reality(device)

    drifts = differ.diff(intent, reality)

    # differ.diff produces records without a "device" field (it only compares
    # two states; it doesn't know whose they are). save_drifts / the schema
    # require one, and the pipeline is the layer that knows the device name —
    # so stamp it onto each record here before persisting.
    for record in drifts:
        record["device"] = device_name

    with session_factory() as session:
        save_drifts(session, drifts)
        session.commit()

    return drifts