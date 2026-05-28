import json
from pathlib import Path

from netdrift.differ import diff

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name):
    """Load a fixture JSON file from tests/fixtures/ by filename."""
    path = FIXTURES_DIR / name
    with open(path) as f:
        return json.load(f)


def strip_detected_at(drifts):
    """Return drift records without the detected_at field, for comparison.

    detected_at is wall-clock time set by diff() at runtime, so it can't be
    predicted in a fixture. We verify it separately, then drop it before
    comparing the rest of the record against the expected answer.
    """
    return [
        {k: v for k, v in record.items() if k != "detected_at"}
        for record in drifts
    ]


def test_enabled_and_description_drift_fixture():
    fixture = load_fixture("enabled_and_description_drift.json")

    result = diff(fixture["intent"], fixture["reality"])

    # Every drift record must carry a detected_at, and it must look like a
    # UTC ISO-8601 timestamp (ends with Z).
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")

    # Compare everything EXCEPT detected_at against the expected answer.
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_ip_address_drift_fixture():
    fixture = load_fixture("ip_address_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_interface_missing_in_reality_fixture():
    fixture = load_fixture("interface_missing_in_reality.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]


def test_interface_missing_in_intent_fixture():
    fixture = load_fixture("interface_missing_in_intent.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_untagged_vlan_drift_fixture():
    fixture = load_fixture("untagged_vlan_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_vlan_missing_in_reality_fixture():
    fixture = load_fixture("vlan_missing_in_reality.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_vlan_name_drift_fixture():
    fixture = load_fixture("vlan_name_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_bgp_neighbor_missing_in_reality_fixture():
    fixture = load_fixture("bgp_neighbor_missing_in_reality.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_bgp_neighbor_missing_in_intent_fixture():
    fixture = load_fixture("bgp_neighbor_missing_in_intent.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_bgp_remote_as_drift_fixture():
    fixture = load_fixture("bgp_remote_as_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_bgp_enabled_drift_fixture():
    fixture = load_fixture("bgp_enabled_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_bgp_description_drift_fixture():
    fixture = load_fixture("bgp_description_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_bgp_session_state_drift_fixture():
    fixture = load_fixture("bgp_session_state_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]

def test_ospf_adjacency_missing_in_reality_fixture():
    fixture = load_fixture("ospf_adjacency_missing_in_reality.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]


def test_ospf_adjacency_missing_in_intent_fixture():
    fixture = load_fixture("ospf_adjacency_missing_in_intent.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]


def test_ospf_area_drift_fixture():
    fixture = load_fixture("ospf_area_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]


def test_ospf_interface_drift_fixture():
    fixture = load_fixture("ospf_interface_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]


def test_ospf_adjacency_state_drift_fixture():
    fixture = load_fixture("ospf_adjacency_state_drift.json")
    result = diff(fixture["intent"], fixture["reality"])
    for record in result:
        assert "detected_at" in record
        assert record["detected_at"].endswith("Z")
    assert strip_detected_at(result) == fixture["expected_drifts"]