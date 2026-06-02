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

import logging
import os

from netdrift import differ, explain as _explain, netbox_client
from netdrift.auto_apply import run_auto_apply
from netdrift.collectors import registry
from netdrift.fingerprint import fingerprint as _fingerprint
from netdrift.storage.database import get_sessionmaker
from netdrift.storage.repository import (
    get_explanations,
    is_device_paused,
    record_collection,
    save_drifts,
    upsert_explanation,
)

log = logging.getLogger(__name__)

# Single source of truth for vendor dispatch: the collector registry (shared
# with cli.py). Adding a vendor is a new self-registering collector module
# (collectors/base.py) — no edit here. Built once at import; run_drift_check
# still takes a `collectors` override so tests can inject fakes.
COLLECTORS = registry.build_collectors()


def _resolve_intent_fn():
    """Return the get_intent callable for the configured source of truth.

    Reads SOURCE_OF_TRUTH from the environment at call time so the env var
    can be set after the module is imported. Defaults to 'netbox'.
    """
    source = os.environ.get("SOURCE_OF_TRUTH", "netbox").lower()
    if source == "nautobot":
        from netdrift import nautobot_client
        return nautobot_client.get_intent
    if source == "netbox":
        return netbox_client.get_intent
    raise ValueError(
        f"Unknown SOURCE_OF_TRUTH '{source}'. Valid values: 'netbox', 'nautobot'."
    )


def generate_explanations(session_factory, drifts, *, llm=None, explain_fn=None):
    """Opt-in: generate and store an AI explanation per new drift fingerprint.

    Runs on the scheduler cycle after drifts are persisted. Gated on an LLM
    provider being configured — explain is off by default, so this is a no-op
    (no network call, no rows written) unless an operator opts in, in which case
    the API falls back to the deterministic diagnose() causes. One explanation
    per fingerprint (generate-once): fingerprints that already have a row are
    skipped, so a recurring drift is explained exactly once. Best-effort: any
    failure is logged and swallowed so it never breaks a poll.

    `llm` and `explain_fn` are injectable for tests.
    """
    if explain_fn is None:
        explain_fn = _explain.explain
    if llm is None:
        llm = _explain._resolve_llm(_explain._config())
    if llm is None:
        return  # feature off — nothing to generate

    # One record per fingerprint; the first wins as the representative drift.
    by_fingerprint = {}
    for drift in drifts:
        by_fingerprint.setdefault(_fingerprint(drift), drift)
    if not by_fingerprint:
        return

    try:
        with session_factory() as session:
            existing = get_explanations(session, by_fingerprint.keys())
            for fp, drift in by_fingerprint.items():
                if fp in existing:
                    continue
                co_occurring = [
                    o for o in drifts
                    if o is not drift and o.get("device") == drift.get("device")
                ]
                result = explain_fn(drift, co_occurring=co_occurring, llm=llm)
                upsert_explanation(
                    session, fp, result["explanation"], result["source"]
                )
            session.commit()
    except Exception:
        log.warning("drift explanation generation failed", exc_info=True)


def run_drift_check(device, *, get_intent=None,
                    collectors=None, session_factory=None,
                    auto_apply_fn=None, schedule_repoll_fn=None,
                    explain_fn=None, explain_llm=None):
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
        auto_apply_fn: callable invoked after drifts are saved to run the
            scheduler auto-apply loop. Defaults to auto_apply.run_auto_apply,
            which self-gates on AUTO_REMEDIATION_ENABLED (a no-op when the
            kill-switch is off). Tests inject a fake.
        schedule_repoll_fn: optional callable(device) forwarded to the
            auto-apply loop; called once after any successful apply to trigger
            a one-shot re-poll. The scheduler passes its own helper; left None
            for the CLI/one-shot path.
        explain_fn / explain_llm: injectable seams for the opt-in AI explanation
            step (tests pass a fake explain callable and/or a fake llm). In
            production both default to None, so the step resolves its provider
            from the environment and is a no-op when the AI feature is off.

    Returns the list of drift records produced by differ.diff (also persisted).

    Raises:
        ValueError if the device's platform has no registered collector.
    """
    if collectors is None:
        collectors = COLLECTORS
    if session_factory is None:
        session_factory = get_sessionmaker()
    if get_intent is None:
        get_intent = _resolve_intent_fn()
    if auto_apply_fn is None:
        auto_apply_fn = run_auto_apply

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

    # differ.diff produces records without "device" or "platform". The pipeline
    # knows both (device from its argument; platform from the intent dict), so
    # stamp them onto each record before persisting. platform is stored so that
    # the remediate API endpoints can dispatch to the correct applier without
    # calling NetBox again.
    for record in drifts:
        record["device"] = device_name
        record["platform"] = platform

    with session_factory() as session:
        save_drifts(session, drifts)
        # Stamp a successful collection (this point is only reached when intent,
        # reality, diff, and persist all succeeded) — the signal device_unreachable
        # SLA detection reads.
        record_collection(session, device_name)
        session.commit()

    # v3.0: after persisting, run the auto-apply loop. It opens its own session
    # via session_factory, self-gates on AUTO_REMEDIATION_ENABLED, and is a
    # no-op on empty drifts. The is_device_paused adapter bridges this module's
    # (session, name) repository convention to run_auto_apply's
    # (name, session) injectable contract.
    auto_apply_fn(
        drifts,
        device,
        session_factory,
        is_device_paused_fn=lambda name, sess: is_device_paused(sess, name),
        schedule_repoll_fn=schedule_repoll_fn,
    )

    # AI1: opt-in, best-effort natural-language explanations. No-op (no network,
    # no writes) unless an LLM provider is configured. Runs last because it may
    # make a network call and must never delay or break persistence/remediation.
    generate_explanations(session_factory, drifts,
                          llm=explain_llm, explain_fn=explain_fn)

    return drifts