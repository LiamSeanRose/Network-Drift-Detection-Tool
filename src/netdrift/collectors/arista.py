from datetime import datetime, timezone

from napalm import get_network_driver


def _build_ip_list(ip_raw):
    
    ips = []
    for address, detail in ip_raw.get("ipv4", {}).items():
        ips.append(f"{address}/{detail['prefix_length']}")
    return sorted(ips)


def get_reality(device):
    driver = get_network_driver("eos")
    conn = driver(
        hostname=device["hostname"],
        username=device["username"],
        password=device["password"],
        optional_args={"enforce_verification": False},
    )
    conn.open()
    try:
        raw_interfaces = conn.get_interfaces()
        raw_ips = conn.get_interfaces_ip()
    finally:
        conn.close()

    interfaces = {}
    for name, data in raw_interfaces.items():
        interfaces[name] = {
            "description": data["description"],
            "enabled": data["is_enabled"],
            "ip_addresses": _build_ip_list(raw_ips.get(name, {})),
        }

    return {
        "device": device["name"],
        "platform": "arista_eos",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "interfaces": interfaces,
    }


if __name__ == "__main__":
    test_device = {
        "name": "core-sw-01",
        "hostname": "172.20.20.11",
        "username": "admin",
        "password": "admin",
    }
    from pprint import pprint
    pprint(get_reality(test_device))