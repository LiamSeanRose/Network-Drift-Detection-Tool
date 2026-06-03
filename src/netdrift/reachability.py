"""reachability.py — lightweight device liveness probe.

A full drift check is expensive: a NetBox call, a collector login, and a diff.
Reachability asks only the cheap question Lighthouse never answered — "is this
device's management plane answering right now?" — with a plain TCP connect to the
management port. It logs in to nothing, so it needs no service account, and it
runs on a faster cadence than drift collection, so the fleet view can show
reachable / unreachable / last-seen without waiting for a full poll.

The probe is injectable (``check_reachability(device, probe=...)``) so the logic
is unit-tested without opening a socket, matching the testable-solo design the
collectors use.
"""

import socket
import time
from datetime import datetime, timezone

# A TCP connect to the management port. SSH (22) is the most portable liveness
# signal across vendors; override per device with ``reachability_port`` in
# devices.yml when a device only exposes a management API (e.g. gNMI 57400).
DEFAULT_PORT = 22
DEFAULT_TIMEOUT = 3.0


def _tcp_probe(host, port, timeout):
    """Open and immediately close a TCP connection. Returns
    ``(reachable, latency_ms, error)``. Never raises — a failed connect is a
    result, not an exception."""
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, (time.monotonic() - start) * 1000.0, None
    except OSError as exc:
        return False, None, str(exc)


def reachability_host(device):
    """The address to probe: the device's management hostname, falling back to
    its name (which in the lab doubles as a resolvable host)."""
    return device.get("hostname") or device.get("host") or device["name"]


def reachability_port(device):
    """The TCP port to probe — an explicit ``reachability_port`` override, else
    the SSH default."""
    port = device.get("reachability_port")
    return int(port) if port else DEFAULT_PORT


def check_reachability(device, *, probe=None, timeout=DEFAULT_TIMEOUT, now=None):
    """Probe one device's management plane and return a status dict.

    Args:
        device: device dict with at least ``name`` (and usually ``hostname``).
        probe: callable(host, port, timeout) -> (reachable, latency_ms, error).
            Defaults to a real TCP connect; tests inject a fake (no socket).
        timeout: per-probe timeout in seconds.
        now: injectable clock for tests; defaults to UTC now.

    Returns ``{name, reachable, latency_ms, error, checked_at}``. ``latency_ms``
    is None when unreachable; ``error`` is None when reachable.
    """
    probe = probe or _tcp_probe
    when = now or datetime.now(tz=timezone.utc)
    reachable, latency_ms, error = probe(
        reachability_host(device), reachability_port(device), timeout
    )
    return {
        "name": device["name"],
        "reachable": reachable,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "error": error,
        "checked_at": when.isoformat(),
    }
