"""tests/test_reachability.py — liveness probe unit tests.

The probe is injectable, so the classification logic is tested without opening a
socket. One test exercises the real TCP probe against a closed local port to
confirm the failure path returns a result rather than raising.
"""

from datetime import datetime, timezone

from netdrift.reachability import (
    DEFAULT_PORT,
    check_reachability,
    reachability_host,
    reachability_port,
)


def test_reachable_result():
    def fake_probe(host, port, timeout):
        return True, 12.34, None

    result = check_reachability(
        {"name": "core-sw-01", "hostname": "10.0.0.1"},
        probe=fake_probe,
        now=datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc),
    )
    assert result["name"] == "core-sw-01"
    assert result["reachable"] is True
    assert result["latency_ms"] == 12.3  # rounded to one decimal
    assert result["error"] is None
    assert result["checked_at"].startswith("2026-06-03T12:00:00")


def test_unreachable_result():
    def fake_probe(host, port, timeout):
        return False, None, "connection refused"

    result = check_reachability({"name": "core-sw-01"}, probe=fake_probe)
    assert result["reachable"] is False
    assert result["latency_ms"] is None
    assert result["error"] == "connection refused"


def test_host_and_port_resolution():
    assert reachability_host({"name": "d", "hostname": "172.20.20.11"}) == "172.20.20.11"
    assert reachability_host({"name": "fallback-name"}) == "fallback-name"
    assert reachability_port({"name": "d"}) == DEFAULT_PORT
    assert reachability_port({"name": "d", "reachability_port": "57400"}) == 57400


def test_probe_receives_resolved_host_port_timeout():
    captured = {}

    def fake_probe(host, port, timeout):
        captured.update(host=host, port=port, timeout=timeout)
        return True, 1.0, None

    check_reachability(
        {"name": "d", "hostname": "1.2.3.4", "reachability_port": 830},
        probe=fake_probe, timeout=2.5,
    )
    assert captured == {"host": "1.2.3.4", "port": 830, "timeout": 2.5}


def test_real_tcp_probe_on_closed_port_returns_unreachable():
    from netdrift.reachability import _tcp_probe
    # Port 1 on loopback is virtually always closed -> immediate refusal.
    reachable, latency_ms, error = _tcp_probe("127.0.0.1", 1, 0.5)
    assert reachable is False
    assert latency_ms is None
    assert error is not None
