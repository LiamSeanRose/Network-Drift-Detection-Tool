"""tests/test_lifecycle.py — warranty / end-of-life status logic.

Pure date arithmetic with an injected ``today``, plus list_lifecycle against a
fake NetBox handle (no live NetBox).
"""

from datetime import date

from netdrift import netbox_client
from netdrift.lifecycle import classify, days_until, lifecycle_summary

TODAY = date(2026, 6, 3)


def test_classify_expired():
    assert classify("2026-06-01", today=TODAY) == "expired"


def test_classify_expiring_within_window():
    assert classify("2026-06-20", within_days=30, today=TODAY) == "expiring"


def test_classify_ok_beyond_window():
    assert classify("2027-01-01", within_days=30, today=TODAY) == "ok"


def test_classify_unknown_when_absent():
    assert classify(None, today=TODAY) is None
    assert classify("", today=TODAY) is None
    assert classify("not-a-date", today=TODAY) is None


def test_days_until():
    assert days_until("2026-06-13", today=TODAY) == 10
    assert days_until("2026-06-01", today=TODAY) == -2
    assert days_until(None, today=TODAY) is None


def test_lifecycle_summary_shape():
    summary = lifecycle_summary("2026-06-20", "2030-01-01", within_days=30, today=TODAY)
    assert summary == {
        "warranty_expiry": "2026-06-20",
        "warranty_status": "expiring",
        "warranty_days_left": 17,
        "end_of_life": "2030-01-01",
        "eol_status": "ok",
        "eol_days_left": days_until("2030-01-01", today=TODAY),
    }


def test_lifecycle_summary_handles_missing_dates():
    summary = lifecycle_summary(None, None, today=TODAY)
    assert summary["warranty_expiry"] is None
    assert summary["warranty_status"] is None
    assert summary["eol_status"] is None


# --- netbox_client.list_lifecycle with a fake nb handle ---------------------

class _FakeDevice:
    def __init__(self, name, custom_fields):
        self.name = name
        self.custom_fields = custom_fields


class _FakeDevices:
    def __init__(self, devices):
        self._devices = devices

    def all(self):
        return list(self._devices)

    def filter(self, **kwargs):
        return list(self._devices)


class _FakeNB:
    def __init__(self, devices):
        self.dcim = type("_dcim", (), {"devices": _FakeDevices(devices)})()


def test_list_lifecycle_reads_custom_fields():
    nb = _FakeNB([
        _FakeDevice("core-sw-01", {"warranty_expiry": "2026-12-31",
                                   "end_of_life": "2028-01-01"}),
        _FakeDevice("core-sw-02", {}),  # no lifecycle custom fields set
    ])
    out = netbox_client.list_lifecycle(nb=nb)
    by_name = {d["name"]: d for d in out}
    assert by_name["core-sw-01"]["warranty_expiry"] == "2026-12-31"
    assert by_name["core-sw-01"]["end_of_life"] == "2028-01-01"
    assert by_name["core-sw-02"]["warranty_expiry"] is None


def test_list_lifecycle_honours_custom_field_names():
    nb = _FakeNB([_FakeDevice("d", {"wty": "2027-01-01", "eol": "2029-01-01"})])
    out = netbox_client.list_lifecycle(nb=nb, warranty_field="wty", eol_field="eol")
    assert out[0]["warranty_expiry"] == "2027-01-01"
    assert out[0]["end_of_life"] == "2029-01-01"
