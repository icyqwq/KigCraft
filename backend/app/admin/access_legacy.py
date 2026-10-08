"""Owner lookups for retained private legacy routes. No legacy executor imports."""
from fastapi import HTTPException
from sqlalchemy import or_, select
from app.db.models import AlbumItem, GenerationJob, GenerationOutput, ReferenceAsset


def queries(route, params):
    result = []
    identity = params.get("job_id")
    if route.startswith("/generation/jobs/") and isinstance(identity, str) and len(identity) <= 100:
        result.append(select(GenerationJob.user_id).where(GenerationJob.id == identity))
    path = params.get("asset_path")
    if not isinstance(path, str) or len(path) > 2048:
        return result
    try:
        if route.startswith("/references/"):
            from app.references.router import _normalized_reference_path
            key = _normalized_reference_path(path)
            result.append(select(ReferenceAsset.user_id).where(ReferenceAsset.object_key == key))
        elif params.get("_generated_asset"):
            from app.core.config import get_settings
            from app.main import _normalized_generated_asset_path, _generated_asset_image_urls, _generated_album_item_id
            key = _normalized_generated_asset_path(path)
            urls = _generated_asset_image_urls(key, get_settings().generated_public_prefix)
            result.append(select(GenerationJob.user_id).join(GenerationOutput, GenerationOutput.job_id == GenerationJob.id)
                .where(or_(GenerationOutput.object_key == key, GenerationOutput.image_url.in_(urls))))
            album = _generated_album_item_id(key)
            result.append(select(AlbumItem.user_id).where(
                AlbumItem.id == album if album else AlbumItem.image_url.in_(urls)))
    except HTTPException:
        # Invalid paths have already been refused; do not reinterpret them as resource keys.
        pass
    return result
