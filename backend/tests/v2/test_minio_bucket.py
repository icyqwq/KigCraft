"""Bucket policy/setup components with a pinned SDK Stubber. No MinIO/network."""
import fnmatch
import json
import sys

import pytest
from botocore.stub import Stubber

from app.assets import repository
from app.maintenance import minio_bucket as bucket
from app.maintenance.orphan_assets import digest
from .test_minio_assets import config


def allows(document, action, resource, prefix=None):
    # Test evaluator is deliberately limited to the generated Allow policy subset.
    for statement in document["Statement"]:
        if statement["Effect"] != "Allow" or action not in statement["Action"]:
            continue
        if not any(fnmatch.fnmatchcase(resource, value) for value in statement["Resource"]):
            continue
        conditions = statement.get("Condition", {})
        if conditions and (prefix is None or not any(fnmatch.fnmatchcase(prefix, pattern)
            for pattern in conditions["StringLike"]["s3:prefix"])):
            continue
        return True
    return False


def absent(stub, cfg):
    stub.add_client_error("head_bucket", service_error_code="404", http_status_code=404,
        expected_params={"Bucket": cfg.v2_s3_bucket})


def present(stub, cfg, *, policy=None, lifecycle=None, versioning=None):
    params = {"Bucket": cfg.v2_s3_bucket}
    stub.add_response("head_bucket", {}, params)
    if policy is None:
        stub.add_client_error("get_bucket_policy", service_error_code="NoSuchBucketPolicy",
            http_status_code=404, expected_params=params)
    else:
        stub.add_response("get_bucket_policy", {"Policy": json.dumps(policy)}, params)
    if lifecycle is None:
        stub.add_client_error("get_bucket_lifecycle_configuration",
            service_error_code="NoSuchLifecycleConfiguration", http_status_code=404, expected_params=params)
    else:
        stub.add_response("get_bucket_lifecycle_configuration", {"Rules": lifecycle}, params)
    stub.add_response("get_bucket_versioning", versioning or {}, params)


def test_policies_limit_roles_objects_and_prefix_listing(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(repository, "make_client", lambda _: pytest.fail("offline policy generation"))
    policies = bucket.policies(cfg)
    assert policies["metrics"]["Statement"] == [{"Effect":"Allow","Action":["admin:Prometheus"]}]
    resource = "arn:aws:s3:::private-assets/v2/uploads/image.png"
    arn = "arn:aws:s3:::private-assets"
    for role in ("application", "migration"):
        policy = policies[role]
        assert allows(policy, "s3:GetObject", resource) and allows(policy, "s3:PutObject", resource)
        for action in ("s3:DeleteObject", "s3:DeleteObjectVersion", "s3:PutObjectAcl", "s3:ListBucket"):
            assert not allows(policy, action, resource)
    assert allows(policies["backup"], "s3:GetObject", resource)
    assert not allows(policies["backup"], "s3:PutObject", resource)
    assert not allows(policies["backup"], "s3:DeleteObject", resource)
    for policy in policies.values():
        assert not allows(policy, "s3:GetObject", "arn:aws:s3:::other-bucket/v2/uploads/image.png")
        assert not allows(policy, "s3:GetObject", arn + "/v20/uploads/image.png")
        assert not allows(policy, "s3:PutBucketPolicy", arn)
        assert not allows(policy, "s3:DeleteBucket", arn)
        assert not allows(policy, "s3:PutLifecycleConfiguration", arn)
    cleanup = policies["cleanup"]
    assert allows(cleanup, "s3:GetBucketVersioning", arn)
    assert allows(cleanup, "s3:DeleteObject", resource)
    assert allows(cleanup, "s3:DeleteObjectVersion", resource)
    assert not allows(cleanup, "s3:PutObject", resource)
    assert allows(cleanup, "s3:ListBucket", arn, "v2/")
    assert allows(cleanup, "s3:ListBucketVersions", arn, "v2/uploads/")
    assert not allows(cleanup, "s3:ListBucket", arn, "")
    assert not allows(cleanup, "s3:ListBucket", arn, "v20/")
    assert allows(policies["provisioner"], "s3:CreateBucket", arn)
    assert not allows(policies["inspector"], "s3:CreateBucket", arn)


def test_policy_artifacts_are_complete_nonsecret_and_never_overwrite(tmp_path, monkeypatch):
    cfg = config(tmp_path)
    monkeypatch.setattr(repository, "make_client", lambda _: pytest.fail("offline"))
    root = tmp_path / "policies"
    result = bucket.write_policies(cfg, root)
    assert not result["applied"] and not result["credentials_included"] and not (root / "INCOMPLETE").exists()
    text = "".join(p.read_text(encoding="utf-8") for p in root.iterdir())
    assert "synthetic-secret" not in text and "synthetic-access" not in text and "minio.invalid" not in text
    assert len(set(result["policy_names"].values())) == 7
    for role, sha in result["policies"].items():
        assert digest(json.loads((root / (role + ".json")).read_text(encoding="utf-8"))) == sha
    with pytest.raises(bucket.BucketError, match="new_absolute"):
        bucket.write_policies(cfg, root)


@pytest.mark.parametrize("region", ["us-east-1", "eu-west-1"])
def test_reviewed_absent_bucket_is_created_private_and_verified_with_pinned_sdk(tmp_path, region):
    cfg = config(tmp_path); cfg.v2_s3_region = region
    client = repository.make_client(cfg)
    with Stubber(client) as stub:
        absent(stub, cfg)
        report = bucket.inspect_bucket(cfg, client=client)
        assert report["state"] == "absent" and report["private"] is None
        absent(stub, cfg)
        args = {"Bucket": cfg.v2_s3_bucket}
        if region != "us-east-1":
            args["CreateBucketConfiguration"] = {"LocationConstraint": region}
        stub.add_response("create_bucket", {}, args)
        present(stub, cfg)
        result = bucket.create_bucket(cfg, report["digest"], client=client)
        assert result["inspection"]["private"] is True
        assert result["inspection"]["versioning"] == "unversioned"
        assert result["identity_policies_attached"] is False
        assert result["inspection"]["identity_policies_verified"] is False
        stub.assert_no_pending_responses()
    client.close()


@pytest.mark.parametrize("condition", ["changed_digest", "existing", "access_denied"])
def test_uncertain_or_existing_bucket_is_not_created_or_modified(tmp_path, condition):
    cfg = config(tmp_path); client = repository.make_client(cfg)
    with Stubber(client) as stub:
        if condition == "existing":
            present(stub, cfg)
        elif condition == "access_denied":
            stub.add_client_error("head_bucket", service_error_code="AccessDenied", http_status_code=403,
                expected_params={"Bucket": cfg.v2_s3_bucket})
        else:
            absent(stub, cfg)
        with pytest.raises(bucket.BucketError, match="already_exists|plan_changed|status_unavailable"):
            bucket.create_bucket(cfg, "wrong", client=client)
        stub.assert_no_pending_responses()
    client.close()


@pytest.mark.parametrize("policy", [
    {"Statement": [{"Effect": "Allow", "Principal": "*", "Action": "s3:GetObject"}]},
    {"Statement": [{"Effect": "Allow", "Principal": {"AWS": "restricted"},
                   "Condition": {"IpAddress": {"aws:SourceIp": "10.0.0.0/8"}}}]},
    {"Statement": "unknown"},
])
def test_resource_grants_are_not_guessed_private(tmp_path, policy):
    cfg = config(tmp_path); client = repository.make_client(cfg)
    with Stubber(client) as stub:
        params = {"Bucket": cfg.v2_s3_bucket}
        stub.add_response("head_bucket", {}, params)
        stub.add_response("get_bucket_policy", {"Policy": json.dumps(policy)}, params)
        with pytest.raises(bucket.BucketError, match="resource_grants"):
            bucket.inspect_bucket(cfg, client=client)
        stub.assert_no_pending_responses()
    client.close()


def test_deny_only_bucket_and_disabled_lifecycle_are_accepted_without_mutation(tmp_path):
    cfg = config(tmp_path); client = repository.make_client(cfg)
    with Stubber(client) as stub:
        present(stub, cfg, policy={"Statement": {"Effect": "Deny", "Principal": "*", "Action": "s3:*"}},
            lifecycle=[{"ID": "off", "Status": "Disabled", "Filter": {"Prefix": "v2/"}, "Expiration": {"Days": 30}}],
            versioning={"Status": "Enabled"})
        report = bucket.inspect_bucket(cfg, client=client)
        assert report["private"] and report["policy"]["state"] == "deny_only"
        assert report["versioning"] == "Enabled"
        stub.assert_no_pending_responses()
    client.close()


def test_enabled_bucket_expiration_is_refused_without_changing_it(tmp_path):
    cfg = config(tmp_path); client = repository.make_client(cfg)
    with Stubber(client) as stub:
        params = {"Bucket": cfg.v2_s3_bucket}
        stub.add_response("head_bucket", {}, params)
        stub.add_client_error("get_bucket_policy", service_error_code="NoSuchBucketPolicy", http_status_code=404,
            expected_params=params)
        stub.add_response("get_bucket_lifecycle_configuration",
            {"Rules": [{"Status": "Enabled", "Filter": {"Prefix": ""}, "Expiration": {"Days": 1}}]}, params)
        with pytest.raises(bucket.BucketError, match="lifecycle_rules_active"):
            bucket.inspect_bucket(cfg, client=client)
        stub.assert_no_pending_responses()
    client.close()


def test_cli_never_prints_sdk_diagnostics_or_credentials(tmp_path, monkeypatch, capsys):
    cfg = config(tmp_path)
    env = tmp_path / "private.env"; env.write_text("", encoding="utf-8")
    monkeypatch.setattr(bucket, "Settings", lambda **_: cfg)
    def failing(_):
        raise RuntimeError("synthetic-secret endpoint internal diagnostic")
    monkeypatch.setattr(bucket, "inspect_bucket", failing)
    monkeypatch.setattr(sys, "argv", ["minio_bucket", "inspect", "--env-file", str(env)])
    with pytest.raises(SystemExit) as exc:
        bucket.main()
    assert exc.value.code == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == {"status": "refused", "code": "bucket_maintenance_unavailable"}
    assert "synthetic-secret" not in output.out + output.err
