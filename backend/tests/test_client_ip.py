from types import SimpleNamespace

from app.core.client_ip import MAX_STORED_IP_LENGTH, resolve_client_ip


def _request(host: str, headers: dict[str, str]):
    return SimpleNamespace(client=SimpleNamespace(host=host), headers=headers)


def test_resolve_client_ip_skips_invalid_forwarded_candidates():
    request = _request(
        "127.0.0.1",
        {"x-forwarded-for": "not-an-ip, 203.0.113.7", "x-real-ip": "198.51.100.4"},
    )

    assert resolve_client_ip(request, "127.0.0.1") == "203.0.113.7"


def test_resolve_client_ip_falls_back_to_real_ip_then_direct_host():
    request = _request(
        "127.0.0.1",
        {"x-forwarded-for": "not-an-ip", "x-real-ip": "198.51.100.4"},
    )

    assert resolve_client_ip(request, "127.0.0.1") == "198.51.100.4"

    long_host = "x" * (MAX_STORED_IP_LENGTH + 10)
    request = _request(long_host, {"x-forwarded-for": "not-an-ip"})

    assert resolve_client_ip(request, "*") == long_host[:MAX_STORED_IP_LENGTH]
