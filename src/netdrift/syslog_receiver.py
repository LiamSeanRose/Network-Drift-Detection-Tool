"""syslog_receiver.py — UDP syslog listener that triggers immediate drift polls.

When a network device sends any syslog message, its source IP is looked up
against the device inventory. If found, a drift check is triggered right away
instead of waiting for the next scheduled poll cycle.

A per-device cooldown (default 30 s) prevents a flapping device from kicking
off rapid-fire polls: once triggered, that device is ignored until the cooldown
expires.

Typical use: the scheduler starts a SyslogReceiver as a daemon thread, then
blocks on its own polling loop. The two run in parallel in the same process.

    receiver = SyslogReceiver(devices, check=_check_one)
    receiver.start()

The _listen / socket loop is intentionally not unit-tested — it is thin I/O
plumbing. _on_message(src_ip) contains all the dispatch logic and is tested
directly in tests/test_syslog_receiver.py without opening any socket.
"""

import socket
import threading
import time

DEFAULT_PORT = 1514
DEFAULT_COOLDOWN = 30  # seconds between triggered polls for the same device


class SyslogReceiver:
    def __init__(self, devices, *, check, port=DEFAULT_PORT, cooldown=DEFAULT_COOLDOWN):
        """
        Args:
            devices: dict {device_name: {hostname, ...}} as returned by
                cli.load_devices. The 'hostname' value is used to match
                incoming syslog source IPs.
            check: callable(device_dict) — run when a poll is triggered.
                In production this is scheduler._check_one; tests inject fakes.
            port: UDP port to listen on (default 1514; docker-compose maps
                external 514 → internal 1514).
            cooldown: minimum seconds between triggered polls for one device.
        """
        # Invert the device map so we can look up by source IP in O(1).
        self._ip_map = {
            details["hostname"]: {"name": name, **details}
            for name, details in devices.items()
        }
        self._check = check
        self._port = port
        self._cooldown = cooldown
        self._last_triggered: dict[str, float] = {}
        self._lock = threading.Lock()

    def start(self):
        """Start the UDP listener in a background daemon thread.

        Daemon threads are killed automatically when the main process exits,
        so no explicit shutdown is needed.

        Returns the thread (mostly for testing; callers rarely need it).
        """
        t = threading.Thread(
            target=self._listen, daemon=True, name="syslog-receiver"
        )
        t.start()
        return t

    def _listen(self):
        """Bind a UDP socket and dispatch every incoming packet."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", self._port))
        print(f"Syslog receiver listening on UDP:{self._port}.")
        while True:
            try:
                _data, addr = sock.recvfrom(4096)
                self._on_message(addr[0])
            except OSError:
                break

    def _on_message(self, src_ip: str):
        """Handle one syslog packet from src_ip.

        Looks up the device, enforces the cooldown, then fires a poll in a
        fresh daemon thread so the listener stays responsive.

        This method is called by _listen but is also the entry point for all
        unit tests (which never touch the socket).
        """
        device = self._ip_map.get(src_ip)
        if device is None:
            return

        name = device["name"]
        now = time.monotonic()

        with self._lock:
            if now - self._last_triggered.get(name, float('-inf')) < self._cooldown:
                return
            self._last_triggered[name] = now

        t = threading.Thread(
            target=self._check,
            args=[device],
            daemon=True,
            name=f"syslog-poll:{name}",
        )
        t.start()
        print(f"Syslog from {src_ip} ({name}): triggered immediate drift check.")
        return t
