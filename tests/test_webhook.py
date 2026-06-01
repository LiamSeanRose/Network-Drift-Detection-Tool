"""tests/test_webhook.py — WebhookDispatcher behaviour.

These tests use pytest-httpserver: a REAL local HTTP server, not a
monkey-patched httpx. That makes the test transport-agnostic and trustworthy —
we assert on what actually arrived over the wire.

Thread discipline (a hard rule from the post-v2.5 plan): every test that starts
the dispatcher's background thread calls `stop()` (which joins it) and asserts
the thread is no longer alive. No `time.sleep()`, no deadline races — `stop()`
enqueues a shutdown sentinel after the work items, and the worker drains FIFO,
so by the time `stop()` returns the dispatch has provably completed.

pytest-httpserver binds to loopback (127.0.0.1), which the dispatcher rejects by
default as an SSRF guard — so dispatch tests construct it with
`allow_private=True` (the WEBHOOK_ALLOW_PRIVATE override).
"""

import pytest

from netdrift import webhook


# ---------------------------------------------------------------------------
# URL validation (no server needed)
# ---------------------------------------------------------------------------

def test_rejects_non_http_scheme():
    with pytest.raises(webhook.WebhookConfigError):
        webhook.WebhookDispatcher("ftp://example.com/hook")


def test_rejects_localhost():
    with pytest.raises(webhook.WebhookConfigError):
        webhook.WebhookDispatcher("http://localhost/hook")


def test_rejects_rfc1918_by_default():
    with pytest.raises(webhook.WebhookConfigError):
        webhook.WebhookDispatcher("http://10.1.2.3/hook")


def test_rejects_link_local():
    with pytest.raises(webhook.WebhookConfigError):
        webhook.WebhookDispatcher("http://169.254.1.1/hook")


def test_allows_rfc1918_with_allow_private():
    # On-prem Slack/PagerDuty: must not raise.
    d = webhook.WebhookDispatcher("http://10.1.2.3/hook", allow_private=True)
    assert d.enabled


def test_allows_public_hostname():
    d = webhook.WebhookDispatcher("https://hooks.example.com/services/abc")
    assert d.enabled


def test_no_url_means_disabled():
    d = webhook.WebhookDispatcher(url=None)
    assert d.enabled is False
    # fire() on a disabled dispatcher is a silent no-op, never an error.
    d.fire("critical_drift", {"device": "core-sw-01"})


# ---------------------------------------------------------------------------
# Dispatch (real local HTTP server)
# ---------------------------------------------------------------------------

def _dispatcher(httpserver, **kwargs):
    kwargs.setdefault("allow_private", True)
    return webhook.WebhookDispatcher(httpserver.url_for("/hook"), **kwargs)


def test_fire_posts_to_webhook_server(httpserver):
    httpserver.expect_request("/hook", method="POST").respond_with_json({"ok": True})
    d = _dispatcher(httpserver)
    t = d.start()
    d.fire("critical_drift", {"device": "core-sw-01", "timestamp": "t", "detail": "x"})
    d.stop()

    assert not t.is_alive()
    assert len(httpserver.log) == 1
    body = httpserver.log[0][0].get_json()
    assert body["event_type"] == "critical_drift"
    assert body["device"] == "core-sw-01"
    assert body["detail"] == "x"


def test_event_not_in_enabled_set_is_skipped(httpserver):
    httpserver.expect_request("/hook", method="POST").respond_with_json({})
    d = _dispatcher(httpserver, events=["critical_drift"])
    t = d.start()
    d.fire("apply_success", {"device": "core-sw-01"})  # not enabled → dropped
    d.stop()

    assert not t.is_alive()
    assert len(httpserver.log) == 0


def test_dispatch_failure_does_not_crash_worker(httpserver, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("connection refused")

    d = _dispatcher(httpserver, post_fn=boom)
    t = d.start()
    with caplog.at_level("WARNING"):
        d.fire("critical_drift", {"device": "core-sw-01"})
        d.stop()

    assert not t.is_alive()  # worker survived the exception and shut down cleanly
    assert any("failed" in r.message.lower() for r in caplog.records)


def test_full_queue_drops_and_warns(caplog):
    # Don't start the worker, so nothing drains the queue. maxsize=1: the first
    # fire fills it, the second has nowhere to go and is dropped with a WARNING.
    d = webhook.WebhookDispatcher(
        "https://hooks.example.com/x", queue_size=1,
    )
    with caplog.at_level("WARNING"):
        d.fire("critical_drift", {"device": "a"})
        d.fire("critical_drift", {"device": "b"})

    assert any("full" in r.message.lower() for r in caplog.records)


def test_url_is_redacted_in_logs(httpserver, caplog):
    # A token in the query string must never reach the logs.
    url = httpserver.url_for("/hook") + "?token=SUPERSECRET"
    httpserver.expect_request("/hook", method="POST").respond_with_json({})
    d = webhook.WebhookDispatcher(url, allow_private=True)
    t = d.start()
    with caplog.at_level("INFO"):
        d.fire("critical_drift", {"device": "core-sw-01"})
        d.stop()

    assert not t.is_alive()
    # Only our own log lines are in scope: third-party loggers (httpx, werkzeug)
    # echo the raw URL, but that's the operator's logging config, not ours. The
    # contract is that netdrift.webhook never emits the token itself.
    ours = [r.getMessage() for r in caplog.records if r.name == "netdrift.webhook"]
    assert ours  # sanity: we did log something
    assert all("SUPERSECRET" not in m for m in ours)


def test_rate_limit_param_is_accepted(httpserver):
    # v3.0 only requires the constructor to accept this (enforcement lands in
    # v3.5). Pin the API surface so v3.5 can tighten it without a signature break.
    d = _dispatcher(httpserver, rate_limit_per_minute=120)
    assert d.enabled
