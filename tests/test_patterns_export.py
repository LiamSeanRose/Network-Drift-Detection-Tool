"""Tests for pattern export + the export↔import round-trip (slice 4).

In-memory SQLite, no lab. Proves GET /known-issues/export round-trips with the
importer: export → wipe → import → export is byte-identical.
"""

import textwrap

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from netdrift.api.app import app, get_session
from netdrift.patterns.exporter import export_known_issues, known_issue_to_pattern
from netdrift.patterns.importer import import_patterns_dir, import_patterns_text
from netdrift.storage.models import Base
from netdrift.storage.repository import (
    list_known_issues,
    save_known_issue,
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

RAW_SNIPPET = """
    name: Interface description mismatch
    object_type: interface
    field: description
    drift_kinds: [value_mismatch]
    cause: Description edited out of band
    fix: Restore the documented description
    remediation:
      kind: raw_snippet
      by_platform:
        arista_eos:
          transport: cli
          body: "interface Ethernet1\\n   description uplink"
"""

DIAGNOSIS_ONLY = """
    name: BGP session down
    object_type: bgp_neighbor
    field: session_state
    drift_kinds: [value_mismatch]
    cause: Peer unreachable
    fix: Investigate the peer
"""


# --- known_issue_to_pattern (reconstruction) ---------------------------------


def test_reconstruct_restore_intent(session):
    upsert_known_issue(
        session, "interface|enabled|value_mismatch", "c", "f",
        remediation={"kind": "restore_intent", "schema_version": 1,
                     "object_type": "interface", "field": "enabled",
                     "drift_kinds": ["value_mismatch"]},
    )
    issue = list_known_issues(session)[0]
    data = known_issue_to_pattern(issue)
    assert data["object_type"] == "interface"
    assert data["field"] == "enabled"
    assert data["drift_kinds"] == ["value_mismatch"]
    # Pattern form drops the importer-derived fields.
    assert data["remediation"] == {"kind": "restore_intent"}


def test_reconstruct_raw_snippet(session):
    upsert_known_issue(
        session, "interface|description|value_mismatch", "c", "f",
        remediation={"kind": "raw_snippet", "schema_version": 1,
                     "by_platform": {"arista_eos": {"transport": "cli", "body": "x"}}},
    )
    data = known_issue_to_pattern(list_known_issues(session)[0])
    assert data["remediation"] == {
        "kind": "raw_snippet",
        "by_platform": {"arista_eos": {"transport": "cli", "body": "x"}},
    }


def test_reconstruct_diagnosis_only(session):
    save_known_issue(session, "bgp_neighbor|session_state|value_mismatch", "c", "f")
    session.flush()
    data = known_issue_to_pattern(list_known_issues(session)[0])
    assert "remediation" not in data


def test_unrepresentable_row_is_skipped(session):
    # Operational field + a remediation is forbidden by the pattern schema, so
    # this hand-created row cannot be expressed as a pattern.
    upsert_known_issue(
        session, "bgp_neighbor|session_state|value_mismatch", "c", "f",
        remediation={"kind": "restore_intent"},
    )
    assert known_issue_to_pattern(list_known_issues(session)[0]) is None
    # And it is dropped from the export, which therefore stays importable.
    assert yaml.safe_load(export_known_issues(session)) == []


# --- round-trip --------------------------------------------------------------


def test_export_is_deterministic_and_sorted(session, tmp_path):
    _write(tmp_path, "z.yaml", RESTORE_INTENT)
    _write(tmp_path, "a.yaml", DIAGNOSIS_ONLY)
    import_patterns_dir(session, tmp_path)
    session.commit()
    out1 = export_known_issues(session)
    out2 = export_known_issues(session)
    assert out1 == out2
    names = [p["object_type"] for p in yaml.safe_load(out1)]
    # Sorted by fingerprint: "bgp_neighbor|..." before "interface|...".
    assert names == ["bgp_neighbor", "interface"]


def test_round_trip_export_wipe_import_export_identical(session, tmp_path):
    _write(tmp_path, "iface.yaml", RESTORE_INTENT)
    _write(tmp_path, "snip.yaml", RAW_SNIPPET)
    _write(tmp_path, "bgp.yaml", DIAGNOSIS_ONLY)
    import_patterns_dir(session, tmp_path)
    session.commit()

    export1 = export_known_issues(session)

    # Wipe every known issue.
    for issue in list_known_issues(session):
        session.delete(issue)
    session.commit()
    assert list_known_issues(session) == []

    # Re-import the exported bundle and export again.
    import_patterns_text(session, export1)
    session.commit()
    export2 = export_known_issues(session)

    assert export1 == export2


def test_import_text_rejects_non_list(session):
    from netdrift.patterns.loader import PatternError

    with pytest.raises(PatternError):
        import_patterns_text(session, "name: not-a-list\n")


def test_import_text_empty_bundle_is_noop(session):
    summary = import_patterns_text(session, "")
    assert summary["created"] == 0
    assert list_known_issues(session) == []


# --- endpoint ----------------------------------------------------------------


@pytest.fixture
def client_with_issues():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        upsert_known_issue(
            s, "interface|enabled|value_mismatch", "shut", "no shut",
            remediation={"kind": "restore_intent", "schema_version": 1,
                         "object_type": "interface", "field": "enabled",
                         "drift_kinds": ["value_mismatch"]},
        )
        s.commit()

    def override_get_session():
        with Session() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_export_endpoint_returns_importable_yaml(client_with_issues):
    resp = client_with_issues.get("/known-issues/export")
    assert resp.status_code == 200
    assert "yaml" in resp.headers["content-type"]
    patterns = yaml.safe_load(resp.text)
    assert len(patterns) == 1
    assert patterns[0]["object_type"] == "interface"
    assert patterns[0]["remediation"] == {"kind": "restore_intent"}


def test_export_endpoint_is_public_get(client_with_issues):
    # No X-API-Key header — GET is public, same as GET /known-issues.
    resp = client_with_issues.get("/known-issues/export")
    assert resp.status_code == 200
