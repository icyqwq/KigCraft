"""Remote asset sources for complete bundles; never populate or trust a local cache."""
from dataclasses import dataclass
import os
from pathlib import Path

from app.assets.repository import MAX_OBJECT_BYTES, MinioStore, checked_target
from app.maintenance.orphan_assets import digest


@dataclass(frozen=True)
class RemoteAsset:
    store: MinioStore
    key: str
    sha256: str
    size: int

    def read(self):
        return self.store.read(self.key, self.sha256, self.size)

    def copy(self, destination):
        data = self.read()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return {"bytes": len(data), "sha256": self.sha256}

    def verify(self):
        data = self.read()
        return {"bytes": len(data), "sha256": self.sha256}


def cache_root(value):
    # It may be empty or absent: remote asset backups must not require a warm cache.
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("absolute_cache_root_required")
    checked_target(root, "backup-path-check")
    return root.resolve()


def repository(settings):
    return MinioStore(settings) if settings.v2_asset_backend == "minio" else None


def source_manifest(settings, files):
    if settings.v2_asset_backend != "minio":
        return {}
    return {"asset_storage": {
        "backend": "minio",
        "target": digest({"endpoint": settings.v2_s3_endpoint_url,
            "bucket": settings.v2_s3_bucket, "prefix": settings.v2_s3_prefix,
            "region": settings.v2_s3_region}),
        "objects": {relative: expected for relative, (source, expected) in files.items()
                    if isinstance(source, RemoteAsset)},
    }}


def valid_manifest(metadata, files):
    if (not isinstance(metadata, dict) or set(metadata) != {"backend", "target", "objects"}
            or metadata["backend"] != "minio" or not isinstance(metadata["target"], str)
            or len(metadata["target"]) != 64
            or any(c not in "0123456789abcdef" for c in metadata["target"])):
        return False
    objects = metadata["objects"]
    if not isinstance(objects, dict) or objects != {
        key: value for key, value in files.items() if key.startswith("assets/")
    }:
        return False
    for value in objects.values():
        if (not isinstance(value, dict) or set(value) != {"bytes", "sha256"}
                or type(value["bytes"]) is not int or not 0 < value["bytes"] <= MAX_OBJECT_BYTES
                or not isinstance(value["sha256"], str) or len(value["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in value["sha256"])):
            return False
    return True
