"""Tests for the pattern library schema and loader (v4.0, slice 1).

Pure validation + fingerprint tests. No database, no lab. Loader tests write
temporary YAML files under pytest's tmp_path.
"""

import textwrap

import pytest

from netdrift.fingerprint import fingerprint as differ_fingerprint
from netdrift.patterns.loader import (
    PatternError,
    load_pattern_file,
    load_patterns_dir,
    pattern_fingerprints,
)
from netdrift.patterns.schema import PatternSchema

# --- helpers -----------------------------------------------------------------


def _base(**overrides):
    data = {
        "name": "Interface admin-down when intent says up",
        "object_type": "interface",
        "field": "enabled",
        "drift_kinds": ["value_mismatch"],
        "cause": "Interface manually shut down or in err-disable",
        "fix": "Re-enable the interface (no shutdown)",
    }
    data.update(overrides)
    return data


def _write(path, mapping_yaml: str):
    path.write_text(textwrap.dedent(mapping_yaml))
    return path


# --- schema: valid shapes ----------------------------------------------------


def test_minimal_diagnosis_only_pattern_is_valid():
    p = PatternSchema.model_validate(_base())
    assert p.remediation is None
    assert p.vendors == []


def test_restore_intent_remediation_is_valid():
    p = PatternSchema.model_validate(_base(remediation={"kind": "restore_intent"}))
    assert p.remediation.kind == "restore_intent"


def test_raw_snippet_cli_remediation_is_valid():
    p = PatternSchema.model_validate(
        _base(
            field="description",
            remediation={
                "kind": "raw_snippet",
                "by_platform": {
                    "arista_eos": {"transport": "cli", "body": "interface Et1\n  description x"},
                },
            },
        )
    )
    assert p.remediation.kind == "raw_snippet"
    assert "arista_eos" in p.remediation.by_platform


def test_raw_snippet_gnmi_remediation_is_valid():
    p = PatternSchema.model_validate(
        _base(
            field="description",
            remediation={
                "kind": "raw_snippet",
                "by_platform": {
                    "nokia_srlinux": {
                        "transport": "gnmi",
                        "updates": [{"path": "/interface[name=ethernet-1/1]/description", "val": "x"}],
                    },
                },
            },
        )
    )
    assert p.remediation.by_platform["nokia_srlinux"].transport == "gnmi"


def test_vendors_list_accepts_canonical_platforms():
    p = PatternSchema.model_validate(_base(vendors=["arista_eos", "cisco_iosxe"]))
    assert p.vendors == ["arista_eos", "cisco_iosxe"]


# --- schema: rejections ------------------------------------------------------


def test_unknown_drift_kind_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(_base(drift_kinds=["totally_made_up"]))


def test_unknown_object_type_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(_base(object_type="firewall_rule"))


def test_extra_key_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(_base(severity="critical"))


def test_empty_drift_kinds_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(_base(drift_kinds=[]))


def test_duplicate_drift_kinds_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(
            _base(drift_kinds=["value_mismatch", "value_mismatch"])
        )


def test_blank_required_field_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(_base(cause="   "))


def test_unknown_vendor_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(_base(vendors=["mikrotik_ros"]))


def test_unknown_platform_in_by_platform_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(
            _base(
                field="description",
                remediation={
                    "kind": "raw_snippet",
                    "by_platform": {"juniper_srx": {"transport": "cli", "body": "x"}},
                },
            )
        )


def test_cli_transport_without_body_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(
            _base(
                field="description",
                remediation={
                    "kind": "raw_snippet",
                    "by_platform": {"arista_eos": {"transport": "cli"}},
                },
            )
        )


def test_gnmi_transport_without_updates_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(
            _base(
                field="description",
                remediation={
                    "kind": "raw_snippet",
                    "by_platform": {"nokia_srlinux": {"transport": "gnmi"}},
                },
            )
        )


def test_cli_transport_with_updates_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(
            _base(
                field="description",
                remediation={
                    "kind": "raw_snippet",
                    "by_platform": {
                        "arista_eos": {"transport": "cli", "body": "x", "updates": [{"a": 1}]},
                    },
                },
            )
        )


def test_operational_field_with_remediation_rejected():
    with pytest.raises(Exception):
        PatternSchema.model_validate(
            _base(
                object_type="bgp_neighbor",
                field="session_state",
                remediation={"kind": "restore_intent"},
            )
        )


def test_operational_field_diagnosis_only_accepted():
    p = PatternSchema.model_validate(
        _base(object_type="bgp_neighbor", field="session_state")
    )
    assert p.remediation is None


# --- fingerprint derivation --------------------------------------------------


def test_fingerprint_matches_differ_formula():
    p = PatternSchema.model_validate(_base())
    # Must equal exactly what a real drift event would fingerprint to.
    expected = differ_fingerprint(
        {"object": "interface:Ethernet1", "field": "enabled", "drift_kind": "value_mismatch"}
    )
    assert pattern_fingerprints(p) == [expected]
    assert pattern_fingerprints(p) == ["interface|enabled|value_mismatch"]


def test_pattern_with_multiple_drift_kinds_maps_to_multiple_fingerprints():
    p = PatternSchema.model_validate(
        _base(field="ip_addresses", drift_kinds=["value_mismatch", "missing_in_reality"])
    )
    assert pattern_fingerprints(p) == [
        "interface|ip_addresses|value_mismatch",
        "interface|ip_addresses|missing_in_reality",
    ]


# --- loader ------------------------------------------------------------------


def test_load_pattern_file_valid(tmp_path):
    f = _write(
        tmp_path / "iface-down.yaml",
        """
        name: Interface down
        object_type: interface
        field: enabled
        drift_kinds: [value_mismatch]
        cause: shut
        fix: no shut
        """,
    )
    p = load_pattern_file(f)
    assert p.name == "Interface down"


def test_load_pattern_file_invalid_yaml_raises_pattern_error(tmp_path):
    f = _write(tmp_path / "bad.yaml", "name: [unterminated\n")
    with pytest.raises(PatternError):
        load_pattern_file(f)


def test_load_pattern_file_non_mapping_raises_pattern_error(tmp_path):
    f = _write(tmp_path / "list.yaml", "- just\n- a\n- list\n")
    with pytest.raises(PatternError):
        load_pattern_file(f)


def test_load_pattern_file_validation_error_includes_path(tmp_path):
    f = _write(
        tmp_path / "wrong.yaml",
        """
        name: bad
        object_type: interface
        field: enabled
        drift_kinds: [nope]
        cause: c
        fix: f
        """,
    )
    with pytest.raises(PatternError) as exc:
        load_pattern_file(f)
    assert "wrong.yaml" in str(exc.value)


def test_load_patterns_dir_loads_all_sorted(tmp_path):
    _write(
        tmp_path / "b.yaml",
        "name: B\nobject_type: vlan\nfield: name\ndrift_kinds: [value_mismatch]\ncause: c\nfix: f\n",
    )
    _write(
        tmp_path / "a.yaml",
        "name: A\nobject_type: interface\nfield: enabled\ndrift_kinds: [value_mismatch]\ncause: c\nfix: f\n",
    )
    loaded = load_patterns_dir(tmp_path)
    assert [p.name for _, p in loaded] == ["A", "B"]


def test_load_patterns_dir_detects_fingerprint_collision(tmp_path):
    body = "object_type: interface\nfield: enabled\ndrift_kinds: [value_mismatch]\ncause: c\nfix: f\n"
    _write(tmp_path / "one.yaml", f"name: One\n{body}")
    _write(tmp_path / "two.yaml", f"name: Two\n{body}")
    with pytest.raises(PatternError) as exc:
        load_patterns_dir(tmp_path)
    assert "collision" in str(exc.value)


def test_load_patterns_dir_empty_raises(tmp_path):
    with pytest.raises(PatternError):
        load_patterns_dir(tmp_path)


def test_load_patterns_dir_not_a_directory_raises(tmp_path):
    f = _write(tmp_path / "x.yaml", "name: x\n")
    with pytest.raises(PatternError):
        load_patterns_dir(f)
