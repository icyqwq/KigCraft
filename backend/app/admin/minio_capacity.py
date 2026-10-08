"""Bounded, read-only MinIO Prometheus capacity sampling. No S3 keys or cache fallback."""
import asyncio
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import json
import re
from urllib.parse import urlsplit

import httpx

from app.core.time import utc_now

MAX_BYTES = 2 * 1024 * 1024
MAX_NUMBER = 2**53 - 1
LABEL = re.compile(r'\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*("(?:[^"\\]|\\["\\n])*")\s*(?:,\s*|$)')
LINE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(.*)\})?\s+([^\s]+)(?:\s+([0-9]+))?\s*')
NAMES = {
    "v3": ("minio_cluster_health_capacity_usable_free_bytes", "minio_cluster_health_capacity_usable_total_bytes",
        "minio_cluster_usage_buckets_total_bytes", "minio_cluster_usage_buckets_quota_total_bytes",
        "minio_cluster_usage_buckets_since_last_update_seconds"),
    "v2": ("minio_cluster_capacity_usable_free_bytes", "minio_cluster_capacity_usable_total_bytes",
        "minio_bucket_usage_total_bytes", "minio_bucket_quota_total_bytes",
        "minio_bucket_usage_last_activity_nano_seconds"),
}


def number(value, *, integral=True):
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("invalid_metric") from None
    if not result.is_finite() or not 0 <= result <= MAX_NUMBER:
        raise ValueError("invalid_metric")
    if integral and result != result.to_integral_value():
        raise ValueError("nonintegral_metric")
    return int(result) if integral else result


def parse(body, names, *, now, max_age):
    result = {name: [] for name in names}
    for line in body.splitlines():
        if not line or line.startswith("#"):
            continue
        head = re.split(r"[{\s]", line, maxsplit=1)[0]
        if head not in result:
            continue
        match = LINE.fullmatch(line)
        if not match:
            raise ValueError("invalid_metric")
        name, raw_labels, value, timestamp = match.groups()
        labels = {}
        text = raw_labels or ""
        position = 0
        while position < len(text):
            item = LABEL.match(text, position)
            if not item or item[1] in labels:
                raise ValueError("invalid_labels")
            labels[item[1]] = json.loads(item[2])
            position = item.end()
        if timestamp is not None:
            age = now.timestamp() - number(timestamp) / 1000
            if age < -5 or age > max_age:
                raise ValueError("stale_metric")
        result[name].append((labels, value))
    return result


def scalar(samples, *, bucket=None, integral=True, divisor=1):
    selected = [value for labels, value in samples
                if (bucket is None and "bucket" not in labels)
                or (bucket is not None and labels.get("bucket") == bucket)]
    if not selected:
        raise ValueError("missing_metric")
    # Cluster totals can be repeated with server labels; never sum them.
    values = {number(value, integral=integral) / divisor for value in selected}
    if len(values) != 1:
        raise ValueError("inconsistent_metric")
    value = values.pop()
    return int(value) if integral else value


def unknown():
    return {"status": "unknown", "free_bytes": None}


async def fetch(client, url, settings):
    try:
        async with asyncio.timeout(settings.v2_minio_metrics_timeout_seconds):
            async with client.stream("GET", url) as response:
                if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                    return None
                content = bytearray()
                async for chunk in response.aiter_raw():
                    content.extend(chunk)
                    if len(content) > MAX_BYTES:
                        return None
                return content.decode("utf-8")
    except (httpx.HTTPError, httpx.InvalidURL, TimeoutError, ValueError, UnicodeError):
        return None


async def measure(settings, *, transport=None, now=None):
    details = {"cluster": unknown(), "bucket": unknown()}
    if not settings.v2_minio_metrics_enabled:
        return details
    try:
        endpoint = urlsplit(settings.v2_s3_endpoint_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
                or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
                or endpoint.path not in {"", "/"}):
            return details
        _ = endpoint.port  # Validate malformed/out-of-range ports before any request.
        origin = settings.v2_s3_endpoint_url.rstrip("/")
        paths = (("/minio/metrics/v3/cluster/health", "/minio/metrics/v3/cluster/usage/buckets")
                 if settings.v2_minio_metrics_version == "v3"
                 else ("/minio/v2/metrics/cluster", "/minio/v2/metrics/bucket"))
        headers = {"Accept": "text/plain", "Accept-Encoding": "identity", "Cache-Control": "no-cache"}
        if settings.v2_minio_metrics_token:
            headers["Authorization"] = "Bearer " + settings.v2_minio_metrics_token
        async with httpx.AsyncClient(headers=headers, timeout=settings.v2_minio_metrics_timeout_seconds,
                follow_redirects=False, trust_env=False, transport=transport) as client:
            bodies = await asyncio.gather(*(fetch(client, origin + path, settings) for path in paths))
        now = now or utc_now()
        free_name, total_name, used_name, quota_name, age_name = NAMES[settings.v2_minio_metrics_version]
        minimum = settings.v2_alert_storage_free_bytes
        try:
            data = parse(bodies[0], [free_name, total_name], now=now, max_age=settings.v2_minio_metrics_max_age_seconds)
            free, total = scalar(data[free_name]), scalar(data[total_name])
            if total <= 0 or free > total:
                raise ValueError("inconsistent_capacity")
            details["cluster"] = {"status": "active" if free < minimum else "clear",
                "free_bytes": free, "total_bytes": total}
        except (ValueError, AttributeError, TypeError):
            pass
        try:
            data = parse(bodies[1], [used_name, quota_name, age_name], now=now,
                max_age=settings.v2_minio_metrics_max_age_seconds)
            used = scalar(data[used_name], bucket=settings.v2_s3_bucket)
            quota = scalar(data[quota_name], bucket=settings.v2_s3_bucket)
            age = scalar(data[age_name], integral=False,
                         divisor=10**9 if settings.v2_minio_metrics_version == "v2" else 1)
            if age > settings.v2_minio_metrics_max_age_seconds:
                raise ValueError("stale_usage")
            free = max(0, quota - used) if quota else None
            details["bucket"] = {"status": "active" if free is not None and free < minimum else "clear",
                "free_bytes": free, "quota_bytes": quota, "used_bytes": used,
                "age_seconds": int(age.to_integral_value(rounding=ROUND_CEILING)), "unlimited": quota == 0}
        except (ValueError, AttributeError, TypeError):
            pass
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        pass
    return details


async def capacity_signal(settings, *, transport=None, now=None):
    from app.admin.v2_health import signal
    details = await measure(settings, transport=transport, now=now)
    states = [value["status"] for value in details.values()]
    count = 1 if "active" in states else 0 if all(state == "clear" for state in states) else None
    values = [value["free_bytes"] for value in details.values() if value["free_bytes"] is not None]
    return {**signal("storage_capacity", count, severity="critical"), "scope": "minio",
        "free_bytes": min(values) if values and count is not None else None,
        "minimum_free_bytes": settings.v2_alert_storage_free_bytes, "capacity": details}
