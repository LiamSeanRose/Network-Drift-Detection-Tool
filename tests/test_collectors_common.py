"""Tests for collectors/_common.py — the shared NAPALM IP-list helper."""

from netdrift.collectors._common import build_ip_list


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
