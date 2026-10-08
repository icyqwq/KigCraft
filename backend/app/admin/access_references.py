"""Allowlisted resource fields only; never walk user prose or arbitrary recipe JSON."""
from __future__ import annotations

MAX_REFERENCES = 128
MAX_BODY_BYTES = 9 * 1024 * 1024

TURN = {
    "revision": ("base_revision_id", "regenerate_revision_id", "continuation.source_revision_id"),
    "message": ("continuation.boundary_message_id",),
    "operation": ("answer_to.operation_id",),
    "quote": ("quote_id",),
    "asset": ("attachments.*.asset_id", "annotation_asset_id", "mask_asset_id",
              "prepared_edit.clean_asset_id", "prepared_edit.root_source_asset_id"),
}
DRAFT = {
    "branch": ("branch_id",), "revision": ("base_revision_id",),
    "asset": ("root_asset_id", "document.attachments.*.asset_id",
              "document.content.localReferences.*.assetId"),
}
CONTINUATION = {"revision": ("continuation.source_revision_id",),
                "message": ("continuation.boundary_message_id",)}
BODY_FIELDS = {
    ("POST", "/v2/branches/{branch_id}/quotes"): TURN,
    ("POST", "/v2/branches/{branch_id}/turns"): TURN,
    ("POST", "/v2/operations/{operation_id}/answers"): TURN,
    ("POST", "/v2/branches/{branch_id}/manual-revisions"): {
        **CONTINUATION, "revision":("base_revision_id","continuation.source_revision_id"),
        "asset":("clean_asset_id",)},
    ("POST", "/v2/branches/{branch_id}/editor-saves"): {
        **CONTINUATION, "revision":("document.baseRevision","continuation.source_revision_id"),
        "asset":("clean_asset_id","document.root.assetId"), "checkpoint":("expected_checkpoint_id",)},
    ("PUT", "/v2/editor-drafts/{draft_id}"): DRAFT,
    ("POST", "/v2/local-images/adopt"): {
        "account":("expected_owner_id",),
        "asset":("clean_asset_id","document.root.assetId","draft.attachments.*.asset_id",
                 "draft.content.localReferences.*.assetId")},
    ("POST", "/v2/branches/{branch_id}/viewset-quotes"): {"revision":("source_revision_id",)},
    ("POST", "/v2/branches/{branch_id}/viewsets"): {
        "revision":("source_revision_id",), "quote":("quote_id",)},
    ("POST", "/v2/viewsets/{identity}/views/{view}/retry"): {"quote":("quote_id",)},
    ("POST", "/v2/viewsets/{identity}/export"): {"revision":("revisions.*",)},
    ("POST", "/v2/viewsets/{identity}/presentation-plan"): {
        "revision":("revisions.*",), "checkpoint":("checkpoints.*",)},
}
QUERY_FIELDS = {
    ("GET", "/v2/branches/{branch_id}/viewset-defaults"): {"revision":("source_revision_id",)},
    ("GET", "/v2/designs/{design_id}"): {"branch":("branch_id",)},
    ("GET", "/v2/designs/{design_id}/revisions"): {"branch":("branch_id",)},
    ("GET", "/v2/branches/{branch_id}/history"): {"message":("message_id",)},
    ("GET", "/v2/branches/{branch_id}/editor-drafts"): {"revision":("base_revision_id",)},
    ("GET", "/v2/branches/{branch_id}/editor-checkpoint"): {"revision":("base_revision_id",)},
    ("POST", "/v2/assets/uploads"): {"account":("expected_owner_id",)},
}


class IncompleteReferences(ValueError):
    pass


def extract_fields(value, fields):
    result = set()
    walked = 0

    def walk(node, parts):
        nonlocal walked
        walked += 1
        if walked > MAX_REFERENCES * 16:
            raise IncompleteReferences()
        if not parts:
            if isinstance(node, str) and 0 < len(node) <= 100:
                yield node
            return
        part, *remaining = parts
        if part == "*":
            if isinstance(node, (list,dict)):
                if len(node) > MAX_REFERENCES:
                    raise IncompleteReferences()
                for item in node.values() if isinstance(node,dict) else node:
                    yield from walk(item, remaining)
        elif isinstance(node,dict) and part in node:
            yield from walk(node[part], remaining)

    for kind, paths in fields.items():
        for path in paths:
            for identity in walk(value, path.split(".")):
                result.add((kind, identity))
                if len(result) > MAX_REFERENCES:
                    raise IncompleteReferences()
    return result


async def request_references(request, route):
    route = route.removeprefix("/api")
    key = (request.method, route)
    try:
        # FastAPI uses the final query value for scalar parameters. This mirrors
        # that behavior; repeated unselected query values are not observed.
        result = extract_fields(dict(request.query_params), QUERY_FIELDS.get(key, {}))
        fields = BODY_FIELDS.get(key)
        album = key == ("POST", "/album/items")
        if album:
            fields = {"album": ()}
        if fields:
            # Only JSON body routes are allowlisted; never consume multipart,
            # uploads, streams, or unrelated request bodies.
            body = await request.body()
            if len(body) > MAX_BODY_BYTES:
                raise IncompleteReferences()
            value = await request.json()
            result.update(extract_fields(value, fields))
            if album and isinstance(value, dict):
                url = value.get("image_url")
                if isinstance(url, str):
                    if len(url) > 4096:
                        raise IncompleteReferences()
                    from app.album.router import _generated_album_item_id_from_url
                    identity = _generated_album_item_id_from_url(url)
                    if identity and len(identity) <= 100:
                        result.add(("album", identity))
        if len(result) > MAX_REFERENCES:
            raise IncompleteReferences()
        return sorted(result), True
    except Exception:
        # Parsing/coverage failures must not change the original error response.
        return [], False
