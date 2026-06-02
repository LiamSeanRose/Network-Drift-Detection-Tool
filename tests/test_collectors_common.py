"""Tests for collectors/_common.py — shared NAPALM collector helpers."""

from netdrift.collectors._common import build_ip_list, normalize_area


def test_build_ip_list_formats_cidr():
    assert build_ip_list({"ipv4": {"10.0.0.1": {"prefix_length": 30}}}) == ["10.0.0.1/30"]


def test_build_ip_list_sorts_multiple():
    raw = {"ipv4": {
        "10.0.0.9": {"prefix_length": 24},
        "10.0.0.1": {"prefix_length": 24},
    }}
    assert build_ip_list(raw) == ["10.0.0.1/24", "10.0.0.9/24"]


def test_build_ip_list_no_ipv4_is_empty():
    assert build_ip_list({}) == []


# --- normalize_area ----------------------------------------------------------

def test_normalize_area_int_zero():
    assert normalize_area(0) == "0.0.0.0"


def test_normalize_area_string_int():
    assert normalize_area("1") == "0.0.0.1"


def test_normalize_area_already_dotted():
    assert normalize_area("0.0.0.1") == "0.0.0.1"


def test_normalize_area_empty_and_none():
    assert normalize_area("") == ""
    assert normalize_area(None) == ""


def test_normalize_area_strips_whitespace():
    assert normalize_area(" 0 ") == "0.0.0.0"


def test_normalize_area_non_numeric_returns_as_is():
    # A value that is neither numeric nor dotted is passed through, not crashed.
    assert normalize_area("backbone") == "backbone"
