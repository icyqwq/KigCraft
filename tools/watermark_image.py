from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.images.watermark import WATERMARK_TEXT, apply_kigcraft_watermark  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply the KigCraft watermark to an image.")
    parser.add_argument("image", type=Path, help="Input image path. Modified in place unless --output is set.")
    parser.add_argument("-o", "--output", type=Path, help="Optional output path. The input image is copied first.")
    parser.add_argument("--force", action="store_true", help="Apply the watermark even if a marker file already exists.")
    parser.add_argument("--logo", type=Path, help="Optional logo path. Defaults to ref/moyulogo.png.")
    parser.add_argument("--text", default=WATERMARK_TEXT, help="Watermark text.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.image.resolve()
    if not source.is_file():
        print(f"Input image does not exist: {source}", file=sys.stderr)
        return 2

    target = args.output.resolve() if args.output else source
    if args.output:
        target.parent.mkdir(parents=True, exist_ok=True)
        if source != target:
            shutil.copy2(source, target)

    logo_path = args.logo.resolve() if args.logo else None
    if logo_path is not None and not logo_path.is_file():
        print(f"Logo image does not exist: {logo_path}", file=sys.stderr)
        return 2

    if not apply_kigcraft_watermark(target, text=args.text, force=args.force, logo_path=logo_path):
        print(f"Failed to watermark image: {target}", file=sys.stderr)
        return 1

    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
