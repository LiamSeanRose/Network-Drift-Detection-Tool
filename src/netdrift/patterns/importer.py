"""patterns/importer.py — turn validated patterns into known_issues rows.

This is the seam between the pattern library (YAML on disk) and the storage
layer. ``import_patterns_dir`` loads + validates a directory of patterns and
upserts each into ``known_issues`` keyed by fingerprint. It is idempotent:
re-running it on the same patterns produces the same database state.

A pattern carries the *intent* of its fix in top-level fields
(``object_type``/``field``/``drift_kinds``). The stored ``restore_intent``
remediation payload (schema.md §9) is derived from those fields here, so a
contributor never hand-writes a fingerprint or duplicates fields inside the
remediation block. A pattern with N ``drift_kinds`` expands to N rows — one per
fingerprint, since ``known_issues.fingerprint`` is unique per single drift_kind.

Imports always land with ``auto_apply_enabled=False`` (enforced in
``upsert_known_issue``); an operator opts a known issue into auto-apply manually
after the confirm-threshold gate, never the importer.
"""

from netdrift.patterns.loader import load_patterns_dir, pattern_fingerprints
from netdrift.patterns.schema import PatternSchema
from netdrift.storage.repository import upsert_known_issue

# Bumped only if the stored remediation payload shape changes (schema.md §9).
REMEDIATION_SCHEMA_VERSION = 1


def _build_remediation(pattern: PatternSchema, drift_kind: str) -> dict | None:
    """Derive the stored remediation payload for one of a pattern's fingerprints.

    ``restore_intent`` records the single drift_kind this fingerprint covers;
    nothing reads ``object_type``/``field``/``drift_kinds`` at apply time (the
    applier renders from the live drift record), but they are stored to match
    the documented shape and keep export round-trips faithful.
    """
    rem = pattern.remediation
    if rem is None:
        return None

    if rem.kind == "restore_intent":
        return {
            "kind": "restore_intent",
            "schema_version": REMEDIATION_SCHEMA_VERSION,
            "object_type": pattern.object_type,
            "field": pattern.field,
            "drift_kinds": [drift_kind],
        }

    # raw_snippet
    return {
        "kind": "raw_snippet",
        "schema_version": REMEDIATION_SCHEMA_VERSION,
        "by_platform": {
            platform: body.model_dump(exclude_none=True)
            for platform, body in rem.by_platform.items()
        },
    }


def pattern_to_known_issues(pattern: PatternSchema) -> list[dict]:
    """Expand one pattern into one importable record per fingerprint.

    Each record is ``{fingerprint, cause, fix, remediation}`` — exactly the
    arguments ``upsert_known_issue`` takes.
    """
    fingerprints = pattern_fingerprints(pattern)
    return [
        {
            "fingerprint": fp,
            "cause": pattern.cause,
            "fix": pattern.fix,
            "remediation": _build_remediation(pattern, drift_kind),
        }
        for drift_kind, fp in zip(pattern.drift_kinds, fingerprints)
    ]


def import_patterns_dir(session, path) -> dict:
    """Load every pattern under ``path`` and upsert it into ``known_issues``.

    Does NOT commit — the caller owns the transaction boundary. Raises
    ``PatternError`` (from the loader) if any file is invalid or two patterns
    collide on a fingerprint, before any row is written.

    Returns a summary: ``{files, created, updated, fingerprints}``.
    """
    loaded = load_patterns_dir(path)

    created = 0
    updated = 0
    fingerprints: list[str] = []

    for _file, pattern in loaded:
        for record in pattern_to_known_issues(pattern):
            _issue, was_created = upsert_known_issue(
                session,
                fingerprint=record["fingerprint"],
                cause=record["cause"],
                fix=record["fix"],
                remediation=record["remediation"],
            )
            fingerprints.append(record["fingerprint"])
            if was_created:
                created += 1
            else:
                updated += 1

    return {
        "files": len(loaded),
        "created": created,
        "updated": updated,
        "fingerprints": fingerprints,
    }
