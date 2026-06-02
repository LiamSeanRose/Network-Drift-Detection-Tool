"""netdrift.patterns — the community pattern library (v4.0).

A *pattern* is a portable, vendor-agnostic description of a recurring drift
(cause + fix, optionally an executable remediation) shipped as a YAML file in
the repo's top-level ``patterns/`` directory. ``driftcheck import-patterns``
loads them, computes each one's fingerprint, and upserts them into the
``known_issues`` table so a fresh install surfaces a stored fix on day one,
before the operator has accumulated any real drift history.

The fingerprint a pattern maps to is computed by the *same* function the differ
uses (``netdrift.fingerprint.fingerprint``), so a pattern can only ever match a
fingerprint the differ actually produces — there is no parallel formula to drift
out of sync.
"""
