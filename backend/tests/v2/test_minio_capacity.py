"""Mock HTTP capacity samples; no actual metrics requests or model calls."""
import asyncio
import json

import httpx
import pytest

from app.admin import minio_capacity as capacity
from app.admin.v2_health import overview
from app.core.time import utc_now
from .test_minio_assets import config
from .test_operations import factory, setup
from .test_migrations import migration_database
from .test_legacy_inventory import fingerprints


def configured(tmp_path, version="v3"):
    cfg = config(tmp_path)
    cfg.v2_minio_metrics_enabled = True
    cfg.v2_minio_metrics_version = version
    cfg.v2_minio_metrics_token = "private-metrics-token"
    cfg.v2_alert_storage_free_bytes = 100
    return cfg


def bodies(cfg, *, free=500, total=1000, used=200, quota=1000, age=1):
    f, t, u, q, a = capacity.NAMES[cfg.v2_minio_metrics_version]
    nanos = 10**9 if cfg.v2_minio_metrics_version == "v2" else 1
    return (f'{f} {free}\n{t} {total}\n',
        f'{u}{{bucket="other"}} 999999\n{u}{{bucket="{cfg.v2_s3_bucket}"}} {used}\n'
        f'{q}{{bucket="{cfg.v2_s3_bucket}"}} {quota}\n{a} {age*nanos}\n')


def mocked(cluster, bucket, requests=None):
    def handle(request):
        if requests is not None:
            requests.append(request)
        body = bucket if request.url.path.endswith(("/bucket", "/buckets")) else cluster
        if isinstance(body, int):
            return httpx.Response(body)
        return httpx.Response(200, stream=httpx.ByteStream(body.encode("utf-8")))
    return httpx.MockTransport(handle)


@pytest.mark.parametrize("version", ["v2", "v3"])
@pytest.mark.parametrize("free,quota,used,status", [(100,1000,200,"clear"),(99,1000,200,"active"),
    (500,1000,901,"active"), (500,0,200,"clear"), (500,1000,1001,"active")])
async def test_cluster_and_target_bucket_boundaries(tmp_path, version, free, quota, used, status):
    cfg = configured(tmp_path, version)
    requests = []
    result = await capacity.capacity_signal(cfg, transport=mocked(*bodies(cfg,free=free,quota=quota,used=used),requests))
    assert result["status"] == status
    assert result["capacity"]["bucket"]["unlimited"] == (quota == 0)
    assert result["capacity"]["bucket"]["used_bytes"] == used
    assert len(requests) == 2 and all(r.method == "GET" for r in requests)
    assert all(r.headers["authorization"] == "Bearer private-metrics-token" for r in requests)
    assert all("synthetic-access" not in str(r.headers) for r in requests)
    assert "private-metrics-token" not in json.dumps(result)


@pytest.mark.parametrize("fault", ["missing", "nan", "negative", "nonintegral", "different_replicas",
    "free_exceeds_total", "stale_timestamp", "future_timestamp"])
async def test_invalid_cluster_metrics_stay_unknown(tmp_path, fault):
    cfg = configured(tmp_path)
    cluster, bucket = bodies(cfg)
    f, t, *_ = capacity.NAMES["v3"]
    if fault == "missing": cluster = f"{t} 1000\n"
    elif fault == "nan": cluster = f"{f} NaN\n{t} 1000\n"
    elif fault == "negative": cluster = f"{f} -1\n{t} 1000\n"
    elif fault == "nonintegral": cluster = f"{f} 1.5\n{t} 1000\n"
    elif fault == "different_replicas": cluster += f'{f}{{server="second"}} 400\n'
    elif fault == "free_exceeds_total": cluster = f"{f} 2000\n{t} 1000\n"
    else:
        stamp = int(utc_now().timestamp()*1000) + (-600000 if fault == "stale_timestamp" else 600000)
        cluster = f"{f} 500 {stamp}\n{t} 1000 {stamp}\n"
    result = await capacity.capacity_signal(cfg, transport=mocked(cluster,bucket))
    assert result["status"] == "unknown" and result["count"] is None
    assert result["capacity"]["cluster"]["status"] == "unknown"
    assert result["capacity"]["bucket"]["status"] == "clear"


async def test_duplicate_cluster_totals_are_not_summed(tmp_path):
    cfg = configured(tmp_path)
    cluster, bucket = bodies(cfg,free=99)
    f, t, *_ = capacity.NAMES["v3"]
    cluster += f'{f}{{server="replica"}} 99\n{t}{{server="replica"}} 1000\n'
    result = await capacity.capacity_signal(cfg, transport=mocked(cluster,bucket))
    assert result["status"] == "active" and result["capacity"]["cluster"]["free_bytes"] == 99


@pytest.mark.parametrize("fault", ["missing_quota", "foreign_bucket", "stale_scan", "malformed_labels"])
async def test_missing_or_stale_bucket_does_not_report_whole_storage_healthy(tmp_path, fault):
    cfg = configured(tmp_path)
    cluster, bucket = bodies(cfg)
    if fault == "missing_quota": bucket = "\n".join(x for x in bucket.splitlines() if "quota" not in x)
    elif fault == "foreign_bucket": bucket = bucket.replace(cfg.v2_s3_bucket, "foreign")
    elif fault == "stale_scan": cluster, bucket = bodies(cfg,age=301)
    else: bucket = bucket.replace('bucket="private-assets"', 'bucket="private-assets",bucket="other"')
    result = await capacity.capacity_signal(cfg, transport=mocked(cluster,bucket))
    assert result["status"] == "unknown" and result["capacity"]["bucket"]["status"] == "unknown"


@pytest.mark.parametrize("failed", ["cluster", "bucket"])
async def test_known_low_capacity_remains_active_when_other_source_fails(tmp_path, failed):
    cfg = configured(tmp_path)
    cluster, bucket = bodies(cfg,free=99,quota=1000,used=990)
    result = await capacity.capacity_signal(cfg,
        transport=mocked(503 if failed=="cluster" else cluster,403 if failed=="bucket" else bucket))
    assert result["status"] == "active"
    assert result["capacity"][failed]["status"] == "unknown"


async def test_disabled_metrics_do_not_make_network_calls_or_use_disk(tmp_path):
    cfg = configured(tmp_path); cfg.v2_minio_metrics_enabled = False
    transport = httpx.MockTransport(lambda _:pytest.fail("network should remain disabled"))
    result = await capacity.capacity_signal(cfg,transport=transport)
    assert result["status"] == "unknown" and result["free_bytes"] is None


async def test_redirect_is_not_followed_and_token_not_forwarded(tmp_path):
    cfg = configured(tmp_path)
    requests = []
    def redirect(request):
        requests.append(request)
        return httpx.Response(302,headers={"Location":"https://other.invalid/collect"})
    result = await capacity.capacity_signal(cfg,transport=httpx.MockTransport(redirect))
    assert result["status"] == "unknown" and len(requests) == 2
    assert all(r.url.host == "minio.invalid" for r in requests)


async def test_bounded_body_and_timeout(tmp_path,monkeypatch):
    cfg = configured(tmp_path)
    monkeypatch.setattr(capacity,"MAX_BYTES",128)
    result = await capacity.capacity_signal(cfg,transport=mocked("x"*129,"x"*129))
    assert result["status"] == "unknown"
    cfg.v2_minio_metrics_timeout_seconds = 0.1
    async def slow(_):
        await asyncio.sleep(1)
        return httpx.Response(200)
    result = await asyncio.wait_for(capacity.capacity_signal(cfg,transport=httpx.MockTransport(slow)),0.5)
    assert result["status"] == "unknown"


async def test_overview_uses_remote_sampler_and_preserves_database(factory,tmp_path,monkeypatch):
    await setup(factory)
    cfg = configured(tmp_path)
    expected = await capacity.capacity_signal(cfg,transport=mocked(*bodies(cfg,free=50)))
    async def observed(settings,**kwargs):
        assert settings is cfg
        return expected
    monkeypatch.setattr(capacity,"capacity_signal",observed)
    monkeypatch.setattr("app.admin.v2_health.shutil.disk_usage",lambda _:pytest.fail("cache is not remote capacity"))
    before = await fingerprints(factory)
    async with factory() as db:
        result = await overview(db,cfg)
    assert next(s for s in result["signals"] if s["code"]=="storage_capacity") == expected
    assert await fingerprints(factory) == before


@pytest.mark.parametrize("endpoint", ["http://user:secret@minio.invalid", "http://minio.invalid/path",
    "ftp://minio.invalid", "http://minio.invalid:invalid"])
async def test_invalid_metric_endpoint_is_unknown_without_requests(tmp_path,endpoint):
    cfg = configured(tmp_path)
    cfg.v2_s3_endpoint_url = endpoint
    transport = httpx.MockTransport(lambda _:pytest.fail("invalid endpoint must not be requested"))
    result = await capacity.capacity_signal(cfg,transport=transport)
    assert result["status"] == "unknown"
