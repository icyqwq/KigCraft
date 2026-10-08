from __future__ import annotations

import json
import struct
import zlib
from pathlib import Path
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
EDIT_RECIPE_KEYWORD = b"kigcraft.edit-recipe"
EDIT_RECIPE_SCHEMA = "kigcraft.edit-recipe"
EDIT_RECIPE_VERSION = 1


def embed_edit_recipe_in_png(image_path: Path, recipe: dict[str, Any]) -> bool:
    """Insert or replace KigCraft edit metadata without re-encoding PNG pixels."""
    content = image_path.read_bytes()
    if not content.startswith(PNG_SIGNATURE):
        return False

    chunks = _parse_chunks(content)
    if chunks is None or not any(chunk_type == b"IEND" for _, _, chunk_type, _ in chunks):
        return False

    payload = json.dumps(
        {
            "recipe": recipe,
            "schema": EDIT_RECIPE_SCHEMA,
            "version": EDIT_RECIPE_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    metadata_chunk = _create_itxt_chunk(payload)

    output = bytearray(PNG_SIGNATURE)
    for start, end, chunk_type, data in chunks:
        if chunk_type in {b"iTXt", b"tEXt"} and _metadata_keyword(data) == EDIT_RECIPE_KEYWORD:
            continue
        if chunk_type == b"IEND":
            output.extend(metadata_chunk)
        output.extend(content[start:end])
    image_path.write_bytes(output)
    return True


def _parse_chunks(content: bytes) -> list[tuple[int, int, bytes, bytes]] | None:
    chunks: list[tuple[int, int, bytes, bytes]] = []
    offset = len(PNG_SIGNATURE)
    while offset + 12 <= len(content):
        length = struct.unpack_from(">I", content, offset)[0]
        data_start = offset + 8
        end = data_start + length + 4
        if end > len(content):
            return None
        chunk_type = content[offset + 4 : offset + 8]
        data = content[data_start : data_start + length]
        chunks.append((offset, end, chunk_type, data))
        offset = end
        if chunk_type == b"IEND":
            return chunks if offset == len(content) else None
    return None


def _create_itxt_chunk(payload: bytes) -> bytes:
    data = EDIT_RECIPE_KEYWORD + b"\0\0\0\0\0" + payload
    chunk_type = b"iTXt"
    checksum = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", checksum)


def _metadata_keyword(data: bytes) -> bytes | None:
    separator = data.find(b"\0")
    return data[:separator] if separator >= 0 else None
