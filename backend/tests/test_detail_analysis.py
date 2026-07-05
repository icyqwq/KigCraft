import shutil
from pathlib import Path

import pytest
from httpx import AsyncClient
from PIL import Image

from app.core.config import get_settings
from app.core.paths import resolve_repo_path
from app.generation.detail_analysis import (
    DetailAnalysisProviderCrop,
    DetailAnalysisProviderRequest,
    DetailAnalysisProviderResult,
    DetailFeature,
    parse_detail_analysis_json,
    persist_detail_crops,
    resolve_detail_bbox_pixels,
)
from app.generation.schemas import DetailCropLockIn, DetailFeatureIn
from app.generation.provider import ImageGenerationProvider, ReferenceRejectedError, get_generation_provider


class DetailAnalysisProvider(ImageGenerationProvider):
    name = "codex"

    def __init__(self) -> None:
        self.request: DetailAnalysisProviderRequest | None = None
        self.call_count = 0

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        self.call_count += 1
        self.request = request
        return DetailAnalysisProviderResult(
            features=[
                DetailFeature(
                    id="feature-hair",
                    kind="hair",
                    label="Hair",
                    description="Light blue long straight hair",
                )
            ],
            crops=[
                DetailAnalysisProviderCrop(
                    id="crop-headwear",
                    kind="headwear",
                    description="Left black X hair clip",
                    source_reference_key="front:references/upload-a/front.webp",
                    bbox={"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4},
                )
            ],
        )


class RemovingSourceDetailAnalysisProvider(DetailAnalysisProvider):
    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        result = await super().analyze_reference_details(request)
        reference_root = resolve_repo_path(get_settings().reference_upload_dir)
        source = reference_root / "upload-a" / "front.webp"
        source.unlink()
        return result


class FailingDetailAnalysisProvider(ImageGenerationProvider):
    name = "codex"

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        raise RuntimeError("upstream credentials expired")


class RejectingDetailAnalysisProvider(ImageGenerationProvider):
    name = "codex"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        raise ReferenceRejectedError(self.reason)


class MixedScopeDetailAnalysisProvider(ImageGenerationProvider):
    name = "codex"

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        return DetailAnalysisProviderResult(
            features=[
                DetailFeature(
                    id="feature-hair",
                    kind="hair",
                    label="Hair",
                    description="Light blue long hair.",
                ),
                DetailFeature(
                    id="feature-clothes",
                    kind="outfit",
                    label="Outfit",
                    description="White sailor uniform.",
                ),
                DetailFeature(
                    id="feature-hands",
                    kind="accessory",
                    label="Hands",
                    description="Hands forming a heart gesture.",
                ),
            ],
            crops=[
                DetailAnalysisProviderCrop(
                    id="crop-eyes",
                    kind="eyes",
                    description="Blue eyes.",
                    source_reference_key="front:references/upload-a/front.webp",
                    bbox={"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.2},
                ),
                DetailAnalysisProviderCrop(
                    id="crop-pose",
                    kind="other",
                    description="Hands forming a heart pose.",
                    source_reference_key="front:references/upload-a/front.webp",
                    bbox={"x": 0.1, "y": 0.55, "width": 0.5, "height": 0.35},
                ),
            ],
        )


class UserRequirementDetailProvider(ImageGenerationProvider):
    name = "codex"

    async def analyze_reference_details(
        self,
        request: DetailAnalysisProviderRequest,
    ) -> DetailAnalysisProviderResult:
        return DetailAnalysisProviderResult(
            features=[
                DetailFeature(
                    id="feature-hair",
                    kind="hair",
                    label="Hair",
                    description="Light blue long hair.",
                ),
                DetailFeature(
                    id="feature-user-requirement",
                    kind="requirement",
                    label="用户要求",
                    description="保留黑色 X 发夹、长直发和委屈表情。",
                ),
            ],
            crops=[],
        )


def test_resolve_detail_bbox_accepts_normalized_and_pixel_coordinates():
    assert resolve_detail_bbox_pixels(
        {"x": 0.25, "y": 0.1, "width": 0.5, "height": 0.4},
        image_width=400,
        image_height=300,
        margin_ratio=0,
    ) == (100, 30, 300, 150)

    assert resolve_detail_bbox_pixels(
        {"x": 20, "y": 15, "width": 80, "height": 40},
        image_width=400,
        image_height=300,
        margin_ratio=0,
    ) == (20, 15, 100, 55)


def test_persist_detail_crops_writes_webp_and_skips_invalid_items(tmp_path):
    source = tmp_path / "refs" / "upload-a" / "front.webp"
    source.parent.mkdir(parents=True)
    Image.new("RGB", (200, 160), "white").save(source)

    crops, warnings = persist_detail_crops(
        analysis_id="analysis-a",
        provider_crops=[
            DetailAnalysisProviderCrop(
                id="crop-1",
                kind="headwear",
                description="Left black X hair clip",
                source_reference_key="front:references/upload-a/front.webp",
                bbox={"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4},
            ),
            DetailAnalysisProviderCrop(
                id="crop-bad",
                kind="eyes",
                description="Invalid tiny crop",
                source_reference_key="front:references/upload-a/front.webp",
                bbox={"x": 0.1, "y": 0.1, "width": 0.001, "height": 0.001},
            ),
        ],
        reference_root=tmp_path / "refs",
        public_prefix="/api/references",
    )

    assert [crop.id for crop in crops] == ["crop-1"]
    assert crops[0].object_key == "references/analysis-a/detail-1.webp"
    assert crops[0].image_url == "/api/references/references/analysis-a/detail-1.webp"
    assert (tmp_path / "refs" / "analysis-a" / "detail-1.webp").is_file()
    assert warnings == ["Skipped crop crop-bad: bbox is too small"]


def test_persist_detail_crops_rejects_unsafe_analysis_id_before_mkdir(tmp_path):
    reference_root = tmp_path / "refs"
    outside = tmp_path / "outside"

    with pytest.raises(ValueError, match="analysis_id is invalid"):
        persist_detail_crops(
            analysis_id="../outside",
            provider_crops=[],
            reference_root=reference_root,
            public_prefix="/api/references",
        )

    assert not outside.exists()


def test_parse_detail_analysis_json_normalizes_codex_aliases_and_bbox_arrays():
    result = parse_detail_analysis_json(
        """
        {
          "features": [
            {
              "id": "feature-face",
              "kind": "face",
              "label": "Soft blush",
              "description": "Round pale face with soft cheek blush."
            },
            {
              "id": "feature-hair",
              "kind": "hairstyle",
              "label": "Long hair",
              "description": "Long light-blue straight hair."
            },
            {
              "id": "feature-hair-detail",
              "kind": "hair_detail",
              "label": "Glossy strands",
              "description": "Hair shine and strand details."
            },
            {
              "id": "feature-clip",
              "kind": "hair_accessory",
              "label": "Black X clip",
              "description": "Black X-shaped hair clip."
            },
            {
              "id": "feature-bracelet",
              "kind": "wrist_accessory",
              "label": "Flower bracelet",
              "description": "Blue flower bracelet."
            },
            {
              "id": "feature-outfit",
              "kind": "clothing",
              "label": "Sailor top",
              "description": "White sailor-style top."
            }
          ],
          "crops": [
            {
              "id": "crop-head",
              "kind": "head_hair",
              "description": "Head and full hair silhouette.",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": [0.07, 0.02, 0.94, 0.72]
            },
            {
              "id": "crop-pose",
              "kind": "pose",
              "description": "Hands forming a heart.",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": [10, 20, 110, 170]
            },
            {
              "id": "crop-eyes",
              "kind": "eye_reference",
              "description": "Blue eyes.",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.1}
            },
            {
              "id": "crop-mouth",
              "kind": "expression",
              "description": "Pout mouth.",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": {"x": 0.45, "y": 0.47, "w": 0.13, "h": 0.07}
            }
          ],
          "warnings": []
        }
        """
    )

    assert [feature.kind for feature in result.features] == ["other", "hair", "hair", "headwear", "accessory", "outfit"]
    assert [crop.kind for crop in result.crops] == ["hair", "other", "eyes", "expression"]
    assert result.crops[0].bbox == pytest.approx({"x": 0.07, "y": 0.02, "width": 0.87, "height": 0.7})
    assert result.crops[1].bbox == {"x": 10, "y": 20, "width": 100, "height": 150}
    assert result.crops[3].bbox == {"x": 0.45, "y": 0.47, "width": 0.13, "height": 0.07}


def test_parse_detail_analysis_json_normalizes_ear_aliases_for_features_and_crops():
    result = parse_detail_analysis_json(
        """
        {
          "features": [
            {
              "id": "feature-cat-ears",
              "kind": "cat_ears",
              "label": "Cat ears",
              "description": "Black cat ears on top of the head."
            },
            {
              "id": "feature-horns",
              "kind": "horns",
              "label": "Horns",
              "description": "Small horn-like head appendages."
            },
            {
              "id": "feature-animal-ear",
              "kind": "animal_ear",
              "label": "Animal ear",
              "description": "Animal ear silhouette."
            }
          ],
          "crops": [
            {
              "id": "crop-elf-ear",
              "kind": "elf_ear",
              "description": "Pointed elf ear.",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": [0.1, 0.2, 0.3, 0.6]
            },
            {
              "id": "crop-horn",
              "kind": "horn",
              "description": "Small horn near the hairline.",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": {"x": 0.5, "y": 0.05, "width": 0.15, "height": 0.2}
            }
          ],
          "warnings": []
        }
        """
    )

    assert [feature.kind for feature in result.features] == ["ears", "ears", "ears"]
    assert [crop.kind for crop in result.crops] == ["ears", "ears"]


def test_detail_kind_schemas_accept_ears() -> None:
    feature = DetailFeatureIn(
        id="feature-ears",
        kind="ears",
        label="Ears",
        description="Cat ears on top of the head.",
    )
    crop = DetailCropLockIn(
        reference_key="front:references/upload-a/front.webp",
        kind="ears",
        description="Cat ears crop.",
    )

    assert feature.kind == "ears"
    assert crop.kind == "ears"


def test_detail_kind_schemas_accept_user_requirement() -> None:
    feature = DetailFeatureIn(
        id="feature-user-requirement",
        kind="requirement",
        label="用户要求",
        description="保留黑色 X 发夹、长直发和委屈表情。",
    )

    assert feature.kind == "requirement"


def test_parse_detail_analysis_json_normalizes_localized_crop_kinds_and_bbox_arrays() -> None:
    result = parse_detail_analysis_json(
        """
        {
          "features": [
            {"id": "feature-hair", "kind": "头发", "label": "刘海", "description": "厚重分层刘海"}
          ],
          "crops": [
            {
              "id": "crop-bangs",
              "kind": "头发",
              "description": "前额刘海",
              "source_reference_key": "front:references/upload-a/front.webp",
              "bbox": [0.68, 0.13, 0.25, 0.74]
            }
          ]
        }
        """
    )

    assert result.features[0].kind == "hair"
    assert result.crops[0].kind == "hair"
    assert result.crops[0].bbox == {"x": 0.68, "y": 0.13, "width": 0.25, "height": 0.74}


async def test_detail_analysis_endpoint_calls_provider_and_serves_crop(
    test_app,
    async_client: AsyncClient,
):
    provider = DetailAnalysisProvider()
    test_app.dependency_overrides[get_generation_provider] = lambda: provider
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    source = reference_root / "upload-a" / "front.webp"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 160), "white").save(source)

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "character_session_id": "session-a",
                "free_text": "keep the X hair clip",
                "requirement_ids": ["keep_expression"],
                "reference_keys": [" front:references/upload-a/front.webp "],
                "reference_descriptions": [
                    {
                        "reference_key": "front:references/upload-a/front.webp",
                        "description": "  front reference  ",
                    }
                ],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["analysis_id"]
        assert payload["features"][0] == {
            "id": "feature-user-requirement",
            "kind": "requirement",
            "label": "用户要求",
            "description": "keep the X hair clip",
            "confidence": None,
        }
        assert payload["features"][1]["description"] == "Light blue long straight hair"
        assert payload["crops"][0]["object_key"].startswith("references/")
        assert payload["crops"][0]["image_url"].startswith("/api/references/references/")
        assert provider.request is not None
        assert provider.request.analysis_id == payload["analysis_id"]
        assert provider.request.character_session_id == "session-a"
        assert provider.request.free_text == "keep the X hair clip"
        assert provider.request.reference_keys == ["front:references/upload-a/front.webp"]
        assert provider.request.reference_descriptions == [
            {
                "reference_key": "front:references/upload-a/front.webp",
                "description": "front reference",
            }
        ]
        assert provider.request.requirement_texts == [
            "preserve the original expression from the reference"
        ]

        crop_response = await async_client.get(payload["crops"][0]["image_url"])
        assert crop_response.status_code == 200
        assert crop_response.headers["content-type"].startswith("image/webp")
    finally:
        test_app.dependency_overrides.clear()
        shutil.rmtree(reference_root / "upload-a", ignore_errors=True)
        if "payload" in locals():
            shutil.rmtree(reference_root / payload["analysis_id"], ignore_errors=True)


async def test_detail_analysis_endpoint_crops_from_reference_snapshot(
    test_app,
    async_client: AsyncClient,
):
    provider = RemovingSourceDetailAnalysisProvider()
    test_app.dependency_overrides[get_generation_provider] = lambda: provider
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    source = reference_root / "upload-a" / "front.webp"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 160), "white").save(source)

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "character_session_id": "session-a",
                "free_text": "",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert [crop["id"] for crop in payload["crops"]] == ["crop-headwear"]
        assert payload["warnings"] == []
        crop_response = await async_client.get(payload["crops"][0]["image_url"])
        assert crop_response.status_code == 200
    finally:
        test_app.dependency_overrides.clear()
        shutil.rmtree(reference_root / "upload-a", ignore_errors=True)
        if "payload" in locals():
            shutil.rmtree(reference_root / payload["analysis_id"], ignore_errors=True)


async def test_detail_analysis_endpoint_places_provider_user_requirement_feature_first(
    test_app,
    async_client: AsyncClient,
):
    test_app.dependency_overrides[get_generation_provider] = lambda: UserRequirementDetailProvider()

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "character_session_id": "session-requirement",
                "free_text": "重点保留黑色 X 发夹、长直发、委屈表情",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert [feature["id"] for feature in payload["features"]] == [
            "feature-user-requirement",
            "feature-hair",
        ]
        assert payload["features"][0]["kind"] == "requirement"
        assert payload["features"][0]["description"] == "保留黑色 X 发夹、长直发和委屈表情。"
    finally:
        test_app.dependency_overrides.clear()


async def test_detail_analysis_endpoint_filters_non_head_provider_details(
    test_app,
    async_client: AsyncClient,
):
    test_app.dependency_overrides[get_generation_provider] = lambda: MixedScopeDetailAnalysisProvider()
    reference_root = resolve_repo_path(get_settings().reference_upload_dir)
    source = reference_root / "upload-a" / "front.webp"
    source.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (200, 160), "white").save(source)

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "character_session_id": "session-filter",
                "free_text": "",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert [feature["id"] for feature in payload["features"]] == ["feature-hair"]
        assert [crop["id"] for crop in payload["crops"]] == ["crop-eyes"]
        assert payload["warnings"] == []
    finally:
        test_app.dependency_overrides.clear()
        shutil.rmtree(reference_root / "upload-a", ignore_errors=True)
        if "payload" in locals():
            shutil.rmtree(reference_root / payload["analysis_id"], ignore_errors=True)


async def test_detail_analysis_passes_normalized_locale_to_provider(
    test_app,
    async_client: AsyncClient,
) -> None:
    for locale, expected_locale in [("en", "en"), ("fr", "zh-CN")]:
        provider = DetailAnalysisProvider()
        test_app.dependency_overrides[get_generation_provider] = lambda: provider
        try:
            response = await async_client.post(
                "/api/generation/detail-analysis",
                json={
                    "character_session_id": f"session-locale-{locale}",
                    "free_text": "",
                    "locale": locale,
                    "reference_keys": [
                        f"front:references/session-locale-{locale}/front.webp"
                    ],
                    "requirement_ids": [],
                },
            )

            assert response.status_code == 200
            assert provider.request is not None
            assert provider.request.locale == expected_locale
        finally:
            test_app.dependency_overrides.clear()


async def test_detail_analysis_endpoint_rejects_blank_reference_keys_without_provider_call(
    test_app,
    async_client: AsyncClient,
):
    provider = DetailAnalysisProvider()
    test_app.dependency_overrides[get_generation_provider] = lambda: provider

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "free_text": "keep the X hair clip",
                "reference_keys": ["", "   "],
            },
        )

        assert response.status_code == 400
        assert provider.call_count == 0
        assert provider.request is None
    finally:
        test_app.dependency_overrides.clear()


async def test_detail_analysis_endpoint_returns_controlled_error_for_provider_failure(
    test_app,
    async_client: AsyncClient,
):
    test_app.dependency_overrides[get_generation_provider] = lambda: FailingDetailAnalysisProvider()

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "free_text": "keep the X hair clip",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

        assert response.status_code == 502
        assert response.json() == {"detail": "detail_analysis_provider_failed: upstream credentials expired"}
    finally:
        test_app.dependency_overrides.clear()


@pytest.mark.parametrize("reason", ["reference_adult_explicit", "reference_unusable"])
async def test_detail_analysis_endpoint_returns_controlled_error_for_rejected_reference(
    reason: str,
    test_app,
    async_client: AsyncClient,
):
    test_app.dependency_overrides[get_generation_provider] = lambda: RejectingDetailAnalysisProvider(reason)

    try:
        response = await async_client.post(
            "/api/generation/detail-analysis",
            json={
                "free_text": "keep the hair",
                "reference_keys": ["front:references/upload-a/front.webp"],
            },
        )

        assert response.status_code == 400
        assert response.json() == {"detail": reason}
    finally:
        test_app.dependency_overrides.clear()


async def test_reference_asset_route_rejects_path_traversal(async_client: AsyncClient):
    response = await async_client.get("/api/references/references/../.env")
    encoded_response = await async_client.get("/api/references/references/%2e%2e/.env")

    assert response.status_code == 400
    assert encoded_response.status_code == 400
