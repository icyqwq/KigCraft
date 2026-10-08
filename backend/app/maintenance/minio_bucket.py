"""Generate scoped IAM policies; inspect or explicitly create a private V2 MinIO bucket."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from botocore.exceptions import ClientError

from app.assets.repository import MinioStore
from app.core.config import Settings
from app.maintenance.orphan_assets import digest, write_new

SCOPE = "v2_minio_bucket"
MAX_POLICY_BYTES = 65536


class BucketError(ValueError):
    pass


def target(settings):
    return digest({"endpoint": settings.v2_s3_endpoint_url, "bucket": settings.v2_s3_bucket,
        "prefix": settings.v2_s3_prefix, "region": settings.v2_s3_region})


def configured(settings, client=None):
    if not settings.v2_enabled or settings.v2_asset_backend != "minio":
        raise BucketError("minio_configuration_required")
    return MinioStore(settings, client=client)


def policies(settings):
    configured(settings, client=object())  # Offline validation; no client or network.
    bucket = "arn:aws:s3:::" + settings.v2_s3_bucket
    objects = bucket + "/" + settings.v2_s3_prefix + "/*"
    def allow(actions, resource, **extra):
        return {"Effect": "Allow", "Action": actions, "Resource": [resource], **extra}
    def policy(statements):
        return {"Version": "2012-10-17", "Statement": statements}
    read = allow(["s3:GetObject"], objects)
    rw = allow(["s3:GetObject", "s3:PutObject"], objects)
    listing = allow(["s3:ListBucket", "s3:ListBucketVersions"], bucket,
        Condition={"StringLike": {"s3:prefix": [settings.v2_s3_prefix + "/*"]}})
    inspect_bucket = allow(["s3:ListBucket", "s3:GetBucketLocation", "s3:GetBucketPolicy",
        "s3:GetBucketVersioning", "s3:GetLifecycleConfiguration"], bucket)
    return {
        "metrics": policy([{"Effect": "Allow", "Action": ["admin:Prometheus"]}]),
        "application": policy([rw]),
        "migration": policy([rw]),
        "backup": policy([read]),
        "cleanup": policy([listing, allow(["s3:GetBucketVersioning"], bucket), allow(["s3:GetObject", "s3:GetObjectVersion",
            "s3:DeleteObject", "s3:DeleteObjectVersion"], objects)]),
        "inspector": policy([inspect_bucket]),
        "provisioner": policy([inspect_bucket, allow(["s3:CreateBucket"], bucket)]),
    }


def write_policies(settings, directory):
    documents = policies(settings)
    directory = Path(directory)
    if not directory.is_absolute() or directory.exists():
        raise BucketError("new_absolute_policy_directory_required")
    if any(p.is_symlink() or p.is_junction() for p in (directory, *directory.parents)):
        raise BucketError("linked_policy_directory")
    directory.mkdir(parents=True, mode=0o700)
    write_new(directory / "INCOMPLETE", {"status": "policy_generation_incomplete"})
    hashes = {}
    for role, document in documents.items():
        write_new(directory / (role + ".json"), document)
        hashes[role] = digest(document)
    manifest = {"format": 1, "scope": SCOPE, "target": target(settings),
        "bucket": settings.v2_s3_bucket, "prefix": settings.v2_s3_prefix,
        "policies": hashes,
        "policy_names": {role: "kigcraft-v2-" + target(settings)[:16] + "-" + role + "-" + sha[:12]
                         for role, sha in hashes.items()},
        "credentials_included": False, "applied": False}
    manifest["digest"] = digest(manifest)
    write_new(directory / "manifest.json", manifest)
    (directory / "INCOMPLETE").unlink()
    return manifest


def error_code(error):
    return error.response.get("Error", {}).get("Code")


def policy_summary(client, bucket):
    try:
        raw = client.get_bucket_policy(Bucket=bucket)["Policy"]
    except ClientError as exc:
        if error_code(exc) == "NoSuchBucketPolicy":
            return {"state": "absent"}
        raise BucketError("bucket_policy_unavailable") from None
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_POLICY_BYTES:
        raise BucketError("invalid_bucket_policy")
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        raise BucketError("invalid_bucket_policy") from None
    statements = value.get("Statement") if isinstance(value, dict) else None
    if isinstance(statements, dict):
        statements = [statements]
    if (not isinstance(statements, list)
            or any(not isinstance(item, dict) or item.get("Effect") != "Deny" for item in statements)):
        # V2 uses identity policies. Do not guess whether a resource Allow with
        # conditions/NotPrincipal is anonymous; require the dedicated-bucket setup.
        raise BucketError("bucket_resource_grants_present")
    return {"state": "deny_only", "digest": digest(value)}


def inspect_with(settings, repo):
    result = {"format": 1, "scope": SCOPE, "target": target(settings)}
    try:
        repo.client.head_bucket(Bucket=repo.bucket)
    except ClientError as exc:
        if error_code(exc) in {"NoSuchBucket", "404", "NotFound"}:
            result.update(state="absent", private=None)
            result["digest"] = digest(result)
            return result
        raise BucketError("bucket_status_unavailable") from None
    policy = policy_summary(repo.client, repo.bucket)
    try:
        lifecycle = repo.client.get_bucket_lifecycle_configuration(Bucket=repo.bucket)
    except ClientError as exc:
        if error_code(exc) != "NoSuchLifecycleConfiguration":
            raise BucketError("bucket_lifecycle_unavailable") from None
        lifecycle = {"Rules": []}
    rules = lifecycle.get("Rules")
    if (not isinstance(rules, list) or any(not isinstance(rule, dict)
            or rule.get("Status") != "Disabled" for rule in rules)):
        raise BucketError("bucket_lifecycle_rules_active")
    versioning = repo.client.get_bucket_versioning(Bucket=repo.bucket)
    state = versioning.get("Status", "unversioned")
    if state not in {"Enabled", "Suspended", "unversioned"}:
        raise BucketError("unknown_bucket_versioning")
    result.update(state="present", private=True, policy=policy,
        lifecycle_digest=digest(rules), versioning=state,
        identity_policies_verified=False)
    result["digest"] = digest(result)
    return result


def inspect_bucket(settings, *, client=None):
    return inspect_with(settings, configured(settings, client=client))


def create_bucket(settings, expected_digest, *, client=None):
    repo = configured(settings, client=client)
    before = inspect_with(settings, repo)
    if before["state"] != "absent":
        raise BucketError("bucket_already_exists")
    if not expected_digest or expected_digest != before["digest"]:
        raise BucketError("bucket_plan_changed")
    parameters = {"Bucket": repo.bucket}
    if settings.v2_s3_region != "us-east-1":
        parameters["CreateBucketConfiguration"] = {"LocationConstraint": settings.v2_s3_region}
    # Default private creation; never delete or rewrite an existing bucket policy.
    repo.client.create_bucket(**parameters)
    after = inspect_with(settings, repo)
    if after["state"] != "present" or not after["private"]:
        raise BucketError("bucket_creation_unconfirmed")
    return {"status": "created", "inspection": after, "identity_policies_attached": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["policies", "inspect", "create"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise BucketError("configuration_file_missing")
        settings = Settings(_env_file=args.env_file)
        if args.command == "policies":
            if args.directory is None:
                raise BucketError("policy_directory_required")
            result = write_policies(settings, args.directory)
        elif args.command == "inspect":
            result = inspect_bucket(settings)
        else:
            result = create_bucket(settings, args.expected_digest)
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": str(exc) if isinstance(exc, BucketError)
                          else "bucket_maintenance_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
