from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from onnxruntime.quantization import CalibrationDataReader, CalibrationMethod, QuantFormat, QuantType, quantize_static
from PIL import Image


MODEL_SIZE = 256
IMAGENET_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


class HrnetCalibrationReader(CalibrationDataReader):
    def __init__(self, image_paths: list[Path], input_name: str = "image") -> None:
        self.input_name = input_name
        self.samples = [sample for path in image_paths for sample in preprocess_image_variants(path)]
        self.index = 0

    def get_next(self) -> dict[str, np.ndarray] | None:
        if self.index >= len(self.samples):
            return None

        sample = self.samples[self.index]
        self.index += 1
        return {self.input_name: sample}


def resize_with_white_padding(image: Image.Image, crop: tuple[int, int, int, int]) -> Image.Image:
    cropped = image.crop(crop)
    scale = min(MODEL_SIZE / max(1, cropped.width), MODEL_SIZE / max(1, cropped.height))
    width = max(1, round(cropped.width * scale))
    height = max(1, round(cropped.height * scale))
    resized = cropped.resize((width, height), Image.Resampling.BICUBIC)
    canvas = Image.new("RGB", (MODEL_SIZE, MODEL_SIZE), "white")
    canvas.paste(resized, ((MODEL_SIZE - width) // 2, (MODEL_SIZE - height) // 2))
    return canvas


def to_tensor(image: Image.Image) -> np.ndarray:
    pixels = np.asarray(image, dtype=np.float32) / 255.0
    pixels = (pixels - IMAGENET_MEAN) / IMAGENET_STD
    return np.transpose(pixels, (2, 0, 1))[None, ...].astype(np.float32)


def preprocess_image_variants(path: Path) -> list[np.ndarray]:
    try:
        image = Image.open(path).convert("RGB")
    except Exception as exc:
        print(f"skip unreadable calibration image {path}: {exc}")
        return []

    width, height = image.size
    square = min(width, height)
    center_crop = ((width - square) // 2, (height - square) // 2, (width + square) // 2, (height + square) // 2)
    upper_height = min(height, max(square, round(height * 0.72)))
    upper_crop = ((width - square) // 2, 0, (width + square) // 2, upper_height)
    full_crop = (0, 0, width, height)

    crops = [full_crop, center_crop]
    if upper_crop[2] > upper_crop[0] and upper_crop[3] > upper_crop[1]:
        crops.append(upper_crop)

    return [to_tensor(resize_with_white_padding(image, crop)) for crop in crops]


def collect_calibration_images(root: Path, limit: int) -> list[Path]:
    candidates = [
        path
        for base in [root / "ref", root / "backend" / "app" / "static" / "fixtures"]
        if base.exists()
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return candidates[:limit]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quantize anime HRNet landmark ONNX model to INT8.")
    parser.add_argument("--input", default="frontend/public/models/anime-face-hrnetv2.onnx")
    parser.add_argument("--output", default="frontend/public/models/anime-face-hrnetv2-int8.onnx")
    parser.add_argument("--calibration-limit", type=int, default=24)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parents[1]
    input_path = root / args.input
    output_path = root / args.output
    calibration_images = collect_calibration_images(root, args.calibration_limit)
    if not calibration_images:
        raise SystemExit("No calibration images found.")

    print(f"input: {input_path}")
    print(f"output: {output_path}")
    print(f"calibration images: {len(calibration_images)}")

    reader = HrnetCalibrationReader(calibration_images)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    quantize_static(
        str(input_path),
        str(output_path),
        reader,
        activation_type=QuantType.QUInt8,
        weight_type=QuantType.QInt8,
        calibrate_method=CalibrationMethod.MinMax,
        quant_format=QuantFormat.QOperator,
        per_channel=True,
    )

    print(f"fp32 bytes: {input_path.stat().st_size}")
    print(f"int8 bytes: {output_path.stat().st_size}")


if __name__ == "__main__":
    main()
