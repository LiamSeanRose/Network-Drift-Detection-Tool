"""tests/test_syslog_receiver.py — unit tests for SyslogReceiver (v0.3).

SyslogReceiver._on_message(src_ip) is the pure dispatch logic — it maps an IP
to a device and decides whether to trigger a poll. All tests call it directly
so no UDP socket is ever opened. threading.Event is used to synchronise with
the background poll threads _on_message spawns, avoiding fragile sleeps.
"""

import threading
import time

from netdrift.syslog_receiver import SyslogReceiver

DEVICES = {
    "core-sw-01": {"hostname": "172.20.20.11", "username": "admin", "password": "x"},
    "core-sw-02": {"hostname": "172.20.20.12", "username": "admin", "password": "x"},
}


def _receiver(check, cooldown=0):
    """Build a SyslogReceiver with cooldown=0 by default so tests don't wait."""
    return SyslogReceiver(DEVICES, check=check, cooldown=cooldown)


def _wait(event, timeout=1.0):
    return event.wait(timeout=timeout)


def test_known_ip_triggers_check():
    """A syslog from a recognised device IP calls check() with that device."""
    triggered = []
    done = threading.Event()

    def fake_check(device):
        triggered.append(device["name"])
        done.set()

    _receiver(fake_check)._on_message("172.20.20.11")
    assert _wait(done), "check was not called within timeout"
    assert triggered == ["core-sw-01"]


def test_check_receives_full_device_dict():
    """check() receives the complete device dict, not just the name."""
    received = {}
    done = threading.Event()

    def fake_check(device):
        received.update(device)
        done.set()

    _receiver(fake_check)._on_message("172.20.20.11")
    _wait(done)
    assert received["name"] == "core-sw-01"
    assert received["hostname"] == "172.20.20.11"


def test_unknown_ip_does_not_trigger():
    """A syslog from an unrecognised IP is silently ignored."""
    triggered = []
    _receiver(lambda d: triggered.append(d))._on_message("10.99.99.99")
    time.sleep(0.05)
    assert triggered == []


def test_cooldown_blocks_repeat_trigger():
    """A second syslog from the same device within the cooldown is ignored."""
    triggered = []
    first_done = threading.Event()

    def fake_check(device):
        triggered.append(device["name"])
        first_done.set()

    receiver = SyslogReceiver(DEVICES, check=fake_check, cooldown=60)
    receiver._on_message("172.20.20.11")
    _wait(first_done)

    # Second message — still inside the 60-second cooldown.
    receiver._on_message("172.20.20.11")
    time.sleep(0.05)
    assert len(triggered) == 1


def test_cooldown_zero_allows_immediate_repeat():
    """cooldown=0 means every syslog triggers a poll, with no wait."""
    call_count = [0]
    events = [threading.Event(), threading.Event()]

    def fake_check(device):
        n = call_count[0]
        call_count[0] += 1
        if n < len(events):
            events[n].set()

    receiver = SyslogReceiver(DEVICES, check=fake_check, cooldown=0)
    receiver._on_message("172.20.20.11")
    _wait(events[0])
    receiver._on_message("172.20.20.11")
    _wait(events[1])
    assert call_count[0] == 2


def test_different_devices_trigger_independently():
    """Syslogs from two distinct devices each trigger their own poll."""
    triggered = []
    sw01_done = threading.Event()
    sw02_done = threading.Event()

    def fake_check(device):
        triggered.append(device["name"])
        if device["name"] == "core-sw-01":
            sw01_done.set()
        else:
            sw02_done.set()

    receiver = _receiver(fake_check)
    receiver._on_message("172.20.20.11")
    receiver._on_message("172.20.20.12")
    _wait(sw01_done)
    _wait(sw02_done)
    assert sorted(triggered) == ["core-sw-01", "core-sw-02"]


def test_empty_device_map_ignores_everything():
    """With no devices configured, no IP triggers a poll."""
    triggered = []
    receiver = SyslogReceiver({}, check=lambda d: triggered.append(d))
    receiver._on_message("172.20.20.11")
    time.sleep(0.05)
    assert triggered == []
