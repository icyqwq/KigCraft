from ipaddress import ip_address, ip_network

from fastapi import Request

MAX_STORED_IP_LENGTH = 64


def resolve_client_ip(request: Request, trusted_proxy_hosts: str) -> str:
    direct_host = request.client.host if request.client else "unknown"
    if not _is_trusted_proxy(direct_host, trusted_proxy_hosts):
        return _bounded_host(direct_host)

    forwarded_for = request.headers.get("x-forwarded-for", "")
    for value in forwarded_for.split(","):
        candidate = _normalized_ip(value.strip())
        if candidate is not None:
            return candidate

    real_ip = _normalized_ip(request.headers.get("x-real-ip", "").strip())
    return real_ip or _bounded_host(direct_host)


def _is_trusted_proxy(host: str, trusted_proxy_hosts: str) -> bool:
    trusted_values = [item.strip() for item in trusted_proxy_hosts.split(",") if item.strip()]
    if "*" in trusted_values:
        return True

    try:
        remote_ip = ip_address(host)
    except ValueError:
        return False

    for trusted_value in trusted_values:
        try:
            if "/" in trusted_value:
                if remote_ip in ip_network(trusted_value, strict=False):
                    return True
            elif remote_ip == ip_address(trusted_value):
                return True
        except ValueError:
            continue
    return False


def _normalized_ip(value: str) -> str | None:
    if not value:
        return None
    try:
        return str(ip_address(value))
    except ValueError:
        return None


def _bounded_host(value: str) -> str:
    return (value or "unknown")[:MAX_STORED_IP_LENGTH]
