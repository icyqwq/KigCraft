"""Private immutable asset objects. Authorization remains in the owning application service."""
from __future__ import annotations
import asyncio
from contextlib import suppress
import base64
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.parse import urlsplit

from app.core.config import get_settings
from app.design.service import ApplicationError

MAX_OBJECT_BYTES = 128 * 1024 * 1024


class StorageUnavailable(OSError):
    def __init__(self):
        super().__init__("asset_storage_unavailable")


def checked_target(root, key):
    parts = PurePosixPath(key).parts
    if (not key or str(PurePosixPath(key)) != key or key.startswith("/") or "\\" in key
        or any(p in {".", ".."} or ":" in p or p.endswith((" ", ".")) for p in parts)):
        raise ApplicationError("invalid_asset_key")
    root = Path(root).absolute()
    path = root.joinpath(*parts)
    if any(p.is_symlink() or p.is_junction() for p in (path, *path.parents)):
        raise ApplicationError("not_found")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ApplicationError("not_found")
    return path


def validate_bytes(data, sha256, size=None):
    if len(data) > MAX_OBJECT_BYTES:
        raise ApplicationError("image_too_large")
    if (size is not None and len(data) != size) or hashlib.sha256(data).hexdigest() != sha256:
        raise ApplicationError("asset_identity_conflict")


def install_file(root, key, data, sha256):
    validate_bytes(data, sha256)
    target = checked_target(root, key)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".asset-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError:
            target = checked_target(root, key)
            if not target.is_file() or target.stat().st_size != len(data):
                raise ApplicationError("asset_identity_conflict") from None
            validate_bytes(target.read_bytes(), sha256, len(data))
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


class FileStore:
    def __init__(self, root):
        self.root = Path(root)
    def put(self, key, data, sha256, mime_type):
        install_file(self.root, key, data, sha256)
    def materialize(self, asset):
        path = checked_target(self.root, asset.object_key)
        if not path.is_file():
            raise ApplicationError("not_found")
        return path

    def delete(self, key):
        """Remove one object; a missing object is already deleted."""
        path = checked_target(self.root, key)
        path.unlink(missing_ok=True)


def make_client(settings):
    # Explicit credentials prevent ambient AWS profiles/metadata service fallback.
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=settings.v2_s3_endpoint_url,
        aws_access_key_id=settings.v2_s3_access_key_id,
        aws_secret_access_key=settings.v2_s3_secret_access_key,
        region_name=settings.v2_s3_region,
        config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
            retries={"total_max_attempts": 1}, s3={"addressing_style": "path"},
            proxies={}, request_checksum_calculation="when_required",
            response_checksum_validation="when_required"))


class MinioStore:
    def __init__(self, settings, *, client=None):
        endpoint = urlsplit(settings.v2_s3_endpoint_url)
        if (endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
            or endpoint.path not in {"", "/"}
            or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", settings.v2_s3_bucket)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_/-]{0,99}", settings.v2_s3_prefix)
            or any(p in {"", ".", ".."} for p in settings.v2_s3_prefix.split("/"))
            or not settings.v2_s3_access_key_id or not settings.v2_s3_secret_access_key):
            raise ApplicationError("asset_storage_not_configured")
        self.root = Path(settings.v2_storage_dir)
        self.bucket = settings.v2_s3_bucket
        self.prefix = settings.v2_s3_prefix
        self.client = client or make_client(settings)

    def remote_key(self, key):
        checked_target(self.root, key)
        return self.prefix + "/" + key

    def read(self, key, sha256, size):
        if type(size) is not int or not 0 <= size <= MAX_OBJECT_BYTES:
            raise ApplicationError("image_too_large")
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.remote_key(key))
            body = response["Body"]
            if response.get("ContentLength") != size:
                raise ApplicationError("asset_identity_conflict")
            chunks, remaining = [], size + 1
            while remaining:
                chunk = body.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            validate_bytes(data, sha256, size)
            return data
        except ApplicationError:
            raise
        except Exception:
            raise StorageUnavailable() from None
        finally:
            if body is not None:
                with suppress(Exception):
                    body.close()

    def put(self, key, data, sha256, mime_type):
        from botocore.exceptions import ClientError
        validate_bytes(data, sha256)
        try:
            self.client.put_object(Bucket=self.bucket, Key=self.remote_key(key), Body=data,
                ContentLength=len(data), ContentType=mime_type, IfNoneMatch="*",
                ContentMD5=base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode(),
                Metadata={"sha256": sha256})
        except ClientError as exc:
            # Only an existing immutable object may be accepted, after byte verification.
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 412:
                raise StorageUnavailable() from None
        except ApplicationError:
            raise
        except Exception:
            raise StorageUnavailable() from None
        self.read(key, sha256, len(data))

    def delete(self, key):
        """Remove the authoritative object, then any warm local copy."""
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self.remote_key(key))
        except ApplicationError:
            raise
        except Exception:
            raise StorageUnavailable() from None
        checked_target(self.root, key).unlink(missing_ok=True)

    def materialize(self, asset):
        # A warm cache cannot mask a missing or changed authoritative remote object.
        data = self.read(asset.object_key, asset.sha256, asset.byte_size)
        return install_file(self.root, asset.object_key, data, asset.sha256)


def asset_store(root=None, *, settings=None):
    if isinstance(root, (FileStore, MinioStore)):
        return root
    settings = settings or get_settings()
    if settings.v2_asset_backend == "filesystem":
        return FileStore(root if root is not None else settings.v2_storage_dir)
    if settings.v2_asset_backend != "minio":
        raise ApplicationError("asset_storage_not_configured")
    if root is not None and Path(root).resolve() != Path(settings.v2_storage_dir).resolve():
        raise ApplicationError("asset_storage_configuration_mismatch")
    return MinioStore(settings)


def require_filesystem(settings):
    if settings.v2_asset_backend != "filesystem":
        raise ApplicationError("filesystem_maintenance_only")


async def storage_io(function, *args, **kwargs):
    """Keep transaction/storage locks until an in-flight object write actually ends."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
