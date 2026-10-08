"""Application-supplied style references: what a finished kigurumi head shell and its four-view sheet look like.

They are added to every ordinary generation turn after the input review (the review judges only what the user supplied)
and always last, so the numbering of the user's own images never shifts. They steer the physical look only; the user's
references stay the sole source of the character.
"""
from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

from app.codex_runtime.session import text_input
from app.contracts.v2 import InputSnapshot

# The images are product photography that a deployment supplies itself (this folder, or KIG_STYLE_REFERENCE_DIR).
# Without them generation runs exactly as before, just without the extra style references.
STYLE_DIR = Path(os.environ.get("KIG_STYLE_REFERENCE_DIR") or Path(__file__).resolve().parents[1] / "static" / "style")
STYLE_FILES = ("finished-head-shell.jpg", "four-view-layout.jpg")
NOTE = (
    "STYLE REFERENCES (added by the application, not by the user). The last two images of this turn are product-style "
    "references only: the first is a photograph of a finished kigurumi head shell with its wig; the second is a four-view "
    "design sheet showing a head shell from four directions in one row. Use them only for the physical look of the finished "
    "product: material finish, wig fibre realism, clean studio lighting, framing and, for a four-view sheet, the row layout. "
    "Never copy their character, face, colours or hairstyle, and never treat them as the user's references; the user's own "
    "images remain the only source of the design."
)


def applies(snapshot: InputSnapshot, operation_kind: str) -> bool:
    """Ordinary generation and edit turns. Not discussion, not local edits (a mask edit must stay inside its region),
    not the clean flat reference (that stage draws no shell)."""
    return operation_kind not in {"discussion", "local_edit"} and not snapshot.local_edit_mode and not snapshot.inline_local \
        and snapshot.target_kind != "reference"


def style_inputs(snapshot: InputSnapshot, operation_kind: str, workspace: Path) -> list[dict]:
    if not applies(snapshot, operation_kind) or not all((STYLE_DIR / name).is_file() for name in STYLE_FILES):
        return []
    directory = workspace / "style"
    directory.mkdir(parents=True, exist_ok=True)
    images = []
    for name in STYLE_FILES:
        source = STYLE_DIR / name
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        target = directory / (digest + ".jpg")
        if not target.exists():
            shutil.copyfile(source, target)
        images.append({"type": "localImage", "path": str(target.resolve())})
    return [text_input(NOTE), *images]
