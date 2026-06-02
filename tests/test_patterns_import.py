"""Tests for pattern import + the upsert_known_issue repository function (slice 3).

Runs against in-memory SQLite — the same fast, lab-free pattern as
tests/test_storage.py. Patterns are written as temporary YAML files.
"""

import textwrap

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from netdrift.patterns.importer import (
    import_patterns_dir,
    pattern_to_known_issues,
)
from netdrift.patterns.schema import PatternSchema
from netdrift.storage.models import Base
from netdrift.storage.repository import (
    get_known_issue,
    list_known_issues,
    set_auto_apply_enabled,
    upsert_known_issue,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s


def _write(path, name, body):
    f = path / name
    f.write_text(textwrap.dedent(body))
    return f


DIAGNOSIS_ONLY = """
    name: BGP session down
    object_type: bgp_neighbor
    field: session_state
    drift_kinds: [value_mismatch]
    cause: Peer unreachable or admin-shut on the far side
    fix: Investigate the peer; confirm reachability and config
"""

RESTORE_INTENT = """
    name: Interface admin-down when intent says up
    object_type: interface
    field: enabled
    drift_kinds: [value_mismatch]
    cause: Interface manually shut down
    fix: Re-enable the interface (no shutdown)
    remediation:
      kind: restore_intent
"""


# --- pattern_to_known_issues (pure) ------------------------------------------


def test_pattern_to_known_issues_record_shape():
    p = PatternSchema.model_validate(
        {
            "name": "x",
            "object_type": "interface",
            "field": "enabled",
            "drift_kinds": ["value_mismatch"],
            "cause": "c",
            "fix": "f",
            "remediation": {"kind": "restore_intent"},
        }
    )
    records = pattern_to_known_issues(p)
    assert len(records) == 1
    rec = records[0]
    assert rec["fingerprint"] == "interface|enabled|value_mismatch"
    assert rec["cause"] == "c"
    assert rec["remediation"]["kind"] == "restore_intent"
    assert rec["remediation"]["object_type"] == "interface"
    assert rec["remediation"]["field"] == "enabled"
    assert rec["remediation"]["drift_kinds"] == ["value_mismatch"]
    assert rec["remediation"]["schema_version"] == 1


def test_raw_snippet_remediation_derived():
    p = PatternSchema.model_validate(
        {
            "name": "x",
            "object_type": "interface",
            "field": "description",
            "drift_kinds": ["value_mismatch"],
            "cause": "c",
            "fix": "f",
            "remediation": {
                "kind": "raw_snippet",
                "by_platform": {
                    "arista_eos": {"transport": "cli", "body": "interface Et1\n  description x"},
                    "nokia_srlinux": {
                        "transport": "gnmi",
                        "updates": [{"path": "/x", "val": "y"}],
                    },
                },
            },
        }
    )
    rem = pattern_to_known_issues(p)[0]["remediation"]
    assert rem["kind"] == "raw_snippet"
    assert rem["by_platform"]["arista_eos"] == {
        "transport": "cli",
        "body": "interface Et1\n  description x",
    }
    assert rem["by_platform"]["nokia_srlinux"] == {
        "transport": "gnmi",
        "updates": [{"path": "/x", "val": "y"}],
    }


def test_diagnosis_only_pattern_has_null_remediation():
    p = PatternSchema.model_validate(
        {
            "name": "x",
            "object_type": "bgp_neighbor",
            "field": "session_state",
            "drift_kinds": ["value_mismatch"],
            "cause": "c",
            "fix": "f",
        }
    )
    assert pattern_to_known_issues(p)[0]["remediation"] is None


def test_multiple_drift_kinds_expand_to_multiple_records():
    p = PatternSchema.model_validate(
        {
            "name": "x",
            "object_type": "interface",
            "field": "ip_addresses",
            "drift_kinds": ["value_mismatch", "missing_in_reality"],
            "cause": "c",
            "fix": "f",
            "remediation": {"kind": "restore_intent"},
        }
    )
    records = pattern_to_known_issues(p)
    assert [r["fingerprint"] for r in records] == [
        "interface|ip_addresses|value_mismatch",
        "interface|ip_addresses|missing_in_reality",
    ]
    # Each row records the single drift_kind its fingerprint encodes.
    assert records[0]["remediation"]["drift_kinds"] == ["value_mismatch"]
    assert records[1]["remediation"]["drift_kinds"] == ["missing_in_reality"]


# --- import_patterns_dir -----------------------------------------------------


def test_import_creates_rows(session, tmp_path):
    _write(tmp_path, "iface.yaml", RESTORE_INTENT)
    _write(tmp_path, "bgp.yaml", DIAGNOSIS_ONLY)
    summary = import_patterns_dir(session, tmp_path)
    session.commit()

    assert summary["files"] == 2
    assert summary["created"] == 2
    assert summary["updated"] == 0

    issues = list_known_issues(session)
    assert len(issues) == 2
    # Every imported row is auto-apply OFF regardless of remediation kind.
    assert all(i.auto_apply_enabled is False for i in issues)


def test_import_is_idempotent(session, tmp_path):
    _write(tmp_path, "iface.yaml", RESTORE_INTENT)
    import_patterns_dir(session, tmp_path)
    session.commit()
    first = {i.fingerprint: (i.cause, i.fix, i.id) for i in list_known_issues(session)}

    summary = import_patterns_dir(session, tmp_path)
    session.commit()
    second = {i.fingerprint: (i.cause, i.fix, i.id) for i in list_known_issues(session)}

    assert summary["created"] == 0
    assert summary["updated"] == 1
    # Same rows (same ids), no duplicates.
    assert first == second
    assert len(list_known_issues(session)) == 1


def test_reimport_refreshes_cause_and_fix(session, tmp_path):
    _write(tmp_path, "iface.yaml", RESTORE_INTENT)
    import_patterns_dir(session, tmp_path)
    session.commit()

    updated = RESTORE_INTENT.replace("Re-enable the interface (no shutdown)", "New fix text")
    _write(tmp_path, "iface.yaml", updated)
    import_patterns_dir(session, tmp_path)
    session.commit()

    issue = get_known_issue(session, "interface|enabled|value_mismatch")
    assert issue.fix == "New fix text"


def test_reimport_preserves_operator_enabled_auto_apply(session, tmp_path):
    _write(tmp_path, "iface.yaml", RESTORE_INTENT)
    import_patterns_dir(session, tmp_path)
    session.commit()

    # Operator opts this known issue into auto-apply.
    issue = get_known_issue(session, "interface|enabled|value_mismatch")
    set_auto_apply_enabled(session, issue.id, True)
    session.commit()

    # Re-importing the pattern must NOT silently disable it.
    import_patterns_dir(session, tmp_path)
    session.commit()
    issue = get_known_issue(session, "interface|enabled|value_mismatch")
    assert issue.auto_apply_enabled is True


def test_import_propagates_pattern_error_on_invalid_file(session, tmp_path):
    from netdrift.patterns.loader import PatternError

    _write(tmp_path, "bad.yaml", "name: x\nobject_type: interface\nfield: enabled\ndrift_kinds: [nope]\ncause: c\nfix: f\n")
    with pytest.raises(PatternError):
        import_patterns_dir(session, tmp_path)


# --- upsert_known_issue (repository unit) ------------------------------------


def test_upsert_inserts_with_auto_apply_off(session):
    issue, created = upsert_known_issue(session, "interface|enabled|value_mismatch", "c", "f")
    assert created is True
    assert issue.auto_apply_enabled is False
    assert issue.remediation is None


def test_upsert_updates_existing_returns_created_false(session):
    upsert_known_issue(session, "fp", "c1", "f1")
    issue, created = upsert_known_issue(session, "fp", "c2", "f2", remediation={"kind": "restore_intent"})
    assert created is False
    assert issue.cause == "c2"
    assert issue.fix == "f2"
    assert issue.remediation == {"kind": "restore_intent"}
    assert len(list_known_issues(session)) == 1


def test_upsert_preserves_auto_apply_on_update(session):
    issue, _ = upsert_known_issue(session, "fp", "c", "f")
    set_auto_apply_enabled(session, issue.id, True)
    issue2, created = upsert_known_issue(session, "fp", "c2", "f2")
    assert created is False
    assert issue2.auto_apply_enabled is True
