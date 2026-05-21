from netdrift.differ import diff


def test_identical_inputs_produce_no_drift():
    intent = {
        "interfaces": {
            "Ethernet1": {"description": "Uplink", "enabled": True, "ip_addresses": []},
        },
    }
    reality = {
        "interfaces": {
            "Ethernet1": {"description": "Uplink", "enabled": True, "ip_addresses": []},
        },
    }
    assert diff(intent, reality) == []


def test_enabled_flip_produces_one_drift():
    intent = {
        "interfaces": {
            "Ethernet1": {"description": "Uplink", "enabled": True, "ip_addresses": []},
        },
    }
    reality = {
        "interfaces": {
            "Ethernet1": {"description": "Uplink", "enabled": False, "ip_addresses": []},
        },
    }
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "enabled"

def test_ip_address_change_produces_one_drift():
    intent = {
        "interfaces": {
            "Ethernet1": {
                "description": "Uplink",
                "enabled": True,
                "ip_addresses": ["10.1.1.5/24"],
            },
        },
    }
    reality = {
        "interfaces": {
            "Ethernet1": {
                "description": "Uplink",
                "enabled": True,
                "ip_addresses": ["10.1.1.9/24"],
            },
        },
    }
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "ip_addresses"

def test_interface_missing_in_reality():
    intent = {
        "interfaces": {
            "Ethernet1": {"description": "Uplink", "enabled": True, "ip_addresses": []},
        },
    }
    reality = {
        "interfaces": {},
    }
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "_interface"
    assert result[0]["drift_kind"] == "missing_in_reality"


def test_interface_missing_in_intent():
    intent = {
        "interfaces": {},
    }
    reality = {
        "interfaces": {
            "Ethernet9": {"description": "Mystery", "enabled": True, "ip_addresses": []},
        },
    }
    result = diff(intent, reality)
    assert len(result) == 1
    assert result[0]["field"] == "_interface"
    assert result[0]["drift_kind"] == "missing_in_intent"

def test_enabled_intent_up_reality_down_is_critical():
    intent = {"interfaces": {"Ethernet1": {"description": "U", "enabled": True, "ip_addresses": []}}}
    reality = {"interfaces": {"Ethernet1": {"description": "U", "enabled": False, "ip_addresses": []}}}
    result = diff(intent, reality)
    assert result[0]["severity"] == "critical"
    assert result[0]["drift_kind"] == "value_mismatch"


def test_enabled_intent_down_reality_up_is_warning():
    intent = {"interfaces": {"Ethernet1": {"description": "U", "enabled": False, "ip_addresses": []}}}
    reality = {"interfaces": {"Ethernet1": {"description": "U", "enabled": True, "ip_addresses": []}}}
    result = diff(intent, reality)
    assert result[0]["severity"] == "warning"


def test_description_drift_is_info():
    intent = {"interfaces": {"Ethernet1": {"description": "Old", "enabled": True, "ip_addresses": []}}}
    reality = {"interfaces": {"Ethernet1": {"description": "New", "enabled": True, "ip_addresses": []}}}
    result = diff(intent, reality)
    assert result[0]["severity"] == "info"


def test_ip_drift_is_warning():
    intent = {"interfaces": {"Ethernet1": {"description": "U", "enabled": True, "ip_addresses": ["10.0.0.1/24"]}}}
    reality = {"interfaces": {"Ethernet1": {"description": "U", "enabled": True, "ip_addresses": ["10.0.0.2/24"]}}}
    result = diff(intent, reality)
    assert result[0]["severity"] == "warning"