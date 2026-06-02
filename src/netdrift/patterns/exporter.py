"""patterns/exporter.py — serialize known_issues back to importable pattern YAML.

The inverse of the importer: read ``known_issues`` rows and emit a YAML list
that ``driftcheck import-patterns`` (or ``import_patterns_text``) can load again.
Output is deterministic — patterns sorted by fingerprint, mapping keys sorted —
so ``export → wipe → import → export`` is byte-for-byte identical.

Lossy by design in one respect: a pattern's human ``name`` and its ``vendors``
list are not columns on ``known_issues`` (only ``fingerprint``/``cause``/``fix``/
``remediation`` are stored), so a round-tripped pattern carries its fingerprint
as ``name`` and omits ``vendors``. The repo's ``patterns/*.yaml`` files remain
the source of truth for those fields; this export reflects current DB state.
"""

import yaml

from netdrift.patterns.schema import PatternSchema
from netdrift.storage.repository import list_known_issues


def known_issue_to_pattern(issue) -> dict | None:
    """Reconstruct a pattern-shaped dict from one known_issue row.

    Returns None if the row cannot be expressed as a valid pattern (e.g. a
    hand-created known issue whose field/remediation combination the pattern
    schema forbids) — so the export is always genuinely importable.
    """
    parts = issue.fingerprint.split("|")
    if len(parts) != 3:
        return None
    object_type, field, drift_kind = parts

    data: dict = {
        "name": issue.fingerprint,
        "object_type": object_type,
        "field": field,
        "drift_kinds": [drift_kind],
        "cause": issue.cause,
        "fix": issue.fix,
    }

    rem = issue.remediation
    if rem:
        kind = rem.get("kind")
        if kind == "restore_intent":
            # The importer re-derives object_type/field/drift_kinds, so the
            # pattern form carries only the kind.
            data["remediation"] = {"kind": "restore_intent"}
        elif kind == "raw_snippet":
            data["remediation"] = {
                "kind": "raw_snippet",
                "by_platform": rem.get("by_platform", {}),
            }

    try:
        PatternSchema.model_validate(data)
    except Exception:
        return None
    return data


def export_known_issues(session) -> str:
    """Return all known_issues as a deterministic YAML list of patterns."""
    issues = sorted(list_known_issues(session), key=lambda i: i.fingerprint)
    patterns = [
        p for p in (known_issue_to_pattern(i) for i in issues) if p is not None
    ]
    return yaml.safe_dump(patterns, sort_keys=True, default_flow_style=False)
