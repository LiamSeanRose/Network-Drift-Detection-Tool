"""webhook.py — outbound webhook / notification dispatch (v3.0).

Fires an HTTP POST when something worth telling a human about happens: a new
critical-severity drift is persisted, or an auto-apply succeeds or fails. The
scheduler and the API are separate processes, so this lives in its own module
and is initialised once per process (in `scheduler.main()` and, for the
API-triggered path, wrapped by FastAPI BackgroundTasks).

Why a queue + daemon thread rather than posting inline? The scheduler's poll
loop must never block on a slow or dead webhook endpoint. `fire()` only
enqueues (non-blocking); a single background worker drains the queue and does
the actual `httpx.post`. If the queue is full we drop the event with a WARNING
rather than apply backpressure to the poller.

Security: the destination URL is validated at construction time to blunt SSRF —
non-HTTP(S) schemes, `localhost`, loopback, RFC 1918, and link-local addresses
are rejected by default. Operators running an on-prem receiver (Slack/PagerDuty
on a private network, or a sidecar on loopback) opt back in with
`WEBHOOK_ALLOW_PRIVATE=true`. The URL is redacted to scheme+host+path in every
log line because tokens are commonly carried in the path or query string.
"""

import ipaddress
import logging
import os
import queue
import threading
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("netdrift.webhook")

DEFAULT_EVENTS = ("critical_drift", "apply_success", "apply_failure")
DEFAULT_QUEUE_SIZE = 100
DEFAULT_TIMEOUT = 5
DEFAULT_RATE_LIMIT_PER_MINUTE = 60

# Sentinel pushed onto the queue by stop() to tell the worker to drain and exit.
_SHUTDOWN = object()


class WebhookConfigError(ValueError):
    """The webhook URL is malformed or points somewhere we refuse to POST to."""


def _redact(url: str) -> str:
    """scheme://host/path — drop userinfo and query, where tokens hide."""
    p = urlparse(url)
    return f"{p.scheme}://{p.hostname or ''}{p.path}"


def _validate_url(url: str, *, allow_private: bool) -> str:
    """Raise WebhookConfigError unless `url` is a safe HTTP(S) destination.

    `allow_private` (the WEBHOOK_ALLOW_PRIVATE override) relaxes the guard for
    all non-public address ranges — loopback, RFC 1918, and link-local — so an
    on-prem deployment can target a receiver on its own host or private network.
    The default (False) rejects every one of them.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise WebhookConfigError(
            f"webhook URL must be http(s), got scheme {parsed.scheme!r}"
        )
    host = parsed.hostname
    if not host:
        raise WebhookConfigError(f"webhook URL has no host: {url!r}")

    if host.lower() == "localhost" and not allow_private:
        raise WebhookConfigError(
            "webhook URL points at localhost; set WEBHOOK_ALLOW_PRIVATE=true to allow"
        )

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # a real hostname (e.g. hooks.slack.com) — allowed.

    if ip is not None and not allow_private:
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            raise WebhookConfigError(
                f"webhook URL points at a private/loopback address ({host}); "
                "set WEBHOOK_ALLOW_PRIVATE=true to allow"
            )
    return url


def _events_from_env() -> tuple[str, ...]:
    raw = os.environ.get("WEBHOOK_EVENTS")
    if not raw:
        return DEFAULT_EVENTS
    return tuple(e.strip() for e in raw.split(",") if e.strip())


class WebhookDispatcher:
    """Enqueue-and-forget webhook sender backed by one daemon worker thread."""

    def __init__(
        self,
        url=None,
        *,
        events=None,
        queue_size=DEFAULT_QUEUE_SIZE,
        timeout=DEFAULT_TIMEOUT,
        rate_limit_per_minute=DEFAULT_RATE_LIMIT_PER_MINUTE,
        allow_private=None,
        post_fn=httpx.post,
    ):
        """
        Args:
            url: destination. Defaults to the WEBHOOK_URL env var. Falsy → the
                dispatcher is disabled and fire() is a no-op (no error).
            events: iterable of event_type strings to actually dispatch.
                Defaults to WEBHOOK_EVENTS env var, else DEFAULT_EVENTS.
            queue_size: bounded queue depth; events beyond this are dropped.
            timeout: per-POST timeout in seconds.
            rate_limit_per_minute: accepted and stored for forward
                compatibility; enforcement is deferred to v3.5 (see roadmap
                emergent issue #5). Stored so v3.5 can tighten it without an
                API change.
            allow_private: override the SSRF guard. Defaults to the
                WEBHOOK_ALLOW_PRIVATE env var.
            post_fn: the POST callable, injectable for tests. Defaults to
                httpx.post.
        """
        if url is None:
            url = os.environ.get("WEBHOOK_URL")
        if events is None:
            events = _events_from_env()
        if allow_private is None:
            allow_private = os.environ.get("WEBHOOK_ALLOW_PRIVATE", "").lower() == "true"

        self._events = set(events)
        self._timeout = timeout
        self._rate_limit_per_minute = rate_limit_per_minute
        self._post = post_fn
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread: threading.Thread | None = None

        self.enabled = bool(url)
        if self.enabled:
            self._url = _validate_url(url, allow_private=allow_private)
            self._redacted = _redact(self._url)
        else:
            self._url = None
            self._redacted = None

    def start(self):
        """Start the background dispatch thread. No-op (returns None) when the
        dispatcher is disabled or already running. Returns the thread."""
        if not self.enabled:
            return None
        if self._thread is not None and self._thread.is_alive():
            return self._thread
        t = threading.Thread(target=self._run, daemon=True, name="webhook-dispatcher")
        t.start()
        self._thread = t
        return t

    def fire(self, event_type: str, payload: dict):
        """Enqueue an event for dispatch. Non-blocking. Drops (with a WARNING)
        if the queue is full, so a slow endpoint can never stall the caller.

        The dispatched body is `{"event_type": event_type, **payload}`; callers
        supply at least `device`, `timestamp`, and `detail`.
        """
        if not self.enabled or event_type not in self._events:
            return
        body = {"event_type": event_type, **payload}
        try:
            self._queue.put_nowait(body)
        except queue.Full:
            logger.warning(
                "Webhook queue full (max %d); dropping %s event for %s.",
                self._queue.maxsize, event_type, payload.get("device"),
            )

    def stop(self, timeout=5.0):
        """Signal the worker to drain remaining items and exit, then join it.

        Used for clean process shutdown and by every test that starts the
        thread (so the test can assert the thread is no longer alive)."""
        if self._thread is None:
            return
        self._queue.put(_SHUTDOWN)
        self._thread.join(timeout=timeout)

    def _run(self):
        """Worker loop: drain the queue until the shutdown sentinel arrives."""
        while True:
            item = self._queue.get()
            if item is _SHUTDOWN:
                break
            self._dispatch(item)

    def _dispatch(self, body: dict):
        """POST one event. A failure is logged, never raised — the worker must
        survive a dead endpoint and keep draining."""
        try:
            resp = self._post(self._url, json=body, timeout=self._timeout)
            logger.info(
                "Webhook %s -> %s (%s)",
                body.get("event_type"), self._redacted,
                getattr(resp, "status_code", "?"),
            )
        except Exception as e:  # noqa: BLE001 — a webhook failure must not kill the daemon
            logger.warning("Webhook POST to %s failed: %s", self._redacted, e)
