import type { FaceBox } from "../editor/deformation/animeLandmarkMapping";
import type { LocalMaskPoint, LocalMaskStroke } from "../editor/localGeneration";

export type MaskBounds = {
  maxX: number;
  maxY: number;
  minX: number;
  minY: number;
};

export type CropRect = {
  height: number;
  width: number;
  x: number;
  y: number;
};

export function isReliableFaceBoxDetection(box: FaceBox, imageWidth: number, imageHeight: number, score?: number) {
  const safeWidth = Math.max(1, imageWidth);
  const safeHeight = Math.max(1, imageHeight);
  const centerX = (box.x + box.width / 2) / safeWidth;
  const centerY = (box.y + box.height / 2) / safeHeight;
  const widthRatio = box.width / safeWidth;
  const heightRatio = box.height / safeHeight;
  const areaRatio = widthRatio * heightRatio;
  const lowConfidence = typeof score === "number" && score > 0 && score < 0.45;
  const outsideLikelyPortraitBand = centerX < 0.25 || centerX > 0.86 || centerY < 0.08 || centerY > 0.72;
  const largePeripheralBox = areaRatio > 0.09 && (centerX < 0.36 || centerX > 0.82);
  const implausiblyWideBox = widthRatio > 0.48 && centerX < 0.4;

  return !lowConfidence && !outsideLikelyPortraitBand && !largePeripheralBox && !implausiblyWideBox;
}

export function createFaceBoxMaskStrokes(box: FaceBox, imageWidth: number, imageHeight: number): LocalMaskStroke[] {
  const safeWidth = Math.max(1, imageWidth);
  const safeHeight = Math.max(1, imageHeight);
  const horizontalPadding = box.width * 0.3;
  const topPadding = box.height * 0.55;
  const bottomPadding = box.height * 0.3;
  const left = Math.max(0, box.x - horizontalPadding);
  const top = Math.max(0, box.y - topPadding);
  const right = Math.min(safeWidth, box.x + box.width + horizontalPadding);
  const bottom = Math.min(safeHeight, box.y + box.height + bottomPadding);
  const width = Math.max(1, right - left);
  const height = Math.max(1, bottom - top);
  const radius = Math.max(8, Math.round(Math.min(width, height) / 2));
  const centerX = left + width / 2;
  const centerY = top + height / 2;
  let points: LocalMaskPoint[];

  if (height >= width) {
    points = [
      { x: clamp(centerX / safeWidth), y: clamp((top + radius) / safeHeight) },
      { x: clamp(centerX / safeWidth), y: clamp((bottom - radius) / safeHeight) },
    ];
  } else {
    points = [
      { x: clamp((left + radius) / safeWidth), y: clamp(centerY / safeHeight) },
      { x: clamp((right - radius) / safeWidth), y: clamp(centerY / safeHeight) },
    ];
  }

  return [{ id: "front-face-mask-1", mode: "brush", points, radius }];
}

export function getMaskBounds(data: Uint8ClampedArray, width: number, height: number): MaskBounds {
  let minX = width;
  let minY = height;
  let maxX = -1;
  let maxY = -1;

  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (data[(y * width + x) * 4 + 3] === 0) continue;
      minX = Math.min(minX, x);
      minY = Math.min(minY, y);
      maxX = Math.max(maxX, x);
      maxY = Math.max(maxY, y);
    }
  }

  if (maxX < minX || maxY < minY) {
    throw new Error("front_reference_mask_empty");
  }

  return { maxX, maxY, minX, minY };
}

export function maskBoundsToCropRect(
  bounds: MaskBounds,
  imageWidth: number,
  imageHeight: number,
  paddingRatio = 0.08,
): CropRect {
  const boundsWidth = bounds.maxX - bounds.minX + 1;
  const boundsHeight = bounds.maxY - bounds.minY + 1;
  const rawPadding = Math.round(Math.max(boundsWidth, boundsHeight) * paddingRatio);
  const padding = paddingRatio > 0 ? Math.max(1, rawPadding) : 0;
  const x = Math.max(0, bounds.minX - padding);
  const y = Math.max(0, bounds.minY - padding);
  const right = Math.min(imageWidth, bounds.maxX + padding + 1);
  const bottom = Math.min(imageHeight, bounds.maxY + padding + 1);
  return {
    height: Math.max(1, bottom - y),
    width: Math.max(1, right - x),
    x,
    y,
  };
}

export function loadImageFromFile(file: File): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const url = URL.createObjectURL(file);
    image.onload = () => {
      URL.revokeObjectURL(url);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(url);
      reject(new Error("front_reference_image_load_failed"));
    };
    image.src = url;
  });
}

export async function cropImageFileWithMask(file: File, strokes: readonly LocalMaskStroke[]): Promise<File> {
  const image = await loadImageFromFile(file);
  return cropImageElementWithMask(file, image, strokes);
}

export function cropImageElementWithMask(
  file: File,
  image: HTMLImageElement,
  strokes: readonly LocalMaskStroke[],
): Promise<File> {
  const width = Math.max(1, Math.round(image.naturalWidth || image.width || 1));
  const height = Math.max(1, Math.round(image.naturalHeight || image.height || 1));
  const maskCanvas = document.createElement("canvas");
  maskCanvas.width = width;
  maskCanvas.height = height;
  renderMask(maskCanvas, strokes);
  const maskContext = maskCanvas.getContext("2d", { willReadFrequently: true });
  if (!maskContext) return Promise.reject(new Error("front_reference_crop_failed"));

  const bounds = getMaskBounds(maskContext.getImageData(0, 0, width, height).data, width, height);
  const crop = maskBoundsToCropRect(bounds, width, height);
  const outputCanvas = document.createElement("canvas");
  outputCanvas.width = crop.width;
  outputCanvas.height = crop.height;
  const outputContext = outputCanvas.getContext("2d");
  if (!outputContext) return Promise.reject(new Error("front_reference_crop_failed"));
  outputContext.drawImage(image, crop.x, crop.y, crop.width, crop.height, 0, 0, crop.width, crop.height);
  outputContext.globalCompositeOperation = "destination-in";
  outputContext.drawImage(maskCanvas, crop.x, crop.y, crop.width, crop.height, 0, 0, crop.width, crop.height);
  outputContext.globalCompositeOperation = "source-over";

  return new Promise((resolve, reject) => {
    outputCanvas.toBlob((blob) => {
      if (!blob) {
        reject(new Error("front_reference_crop_failed"));
        return;
      }
      resolve(new File([blob], `${stripExtension(file.name)}-face.png`, { type: "image/png" }));
    }, "image/png");
  });
}

function renderMask(canvas: HTMLCanvasElement, strokes: readonly LocalMaskStroke[]) {
  const context = canvas.getContext("2d");
  if (!context) return;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.lineCap = "round";
  context.lineJoin = "round";
  context.strokeStyle = "rgba(255,255,255,1)";
  context.fillStyle = "rgba(255,255,255,1)";

  for (const stroke of strokes) {
    if (!stroke.points.length) continue;
    context.globalCompositeOperation = stroke.mode === "erase" ? "destination-out" : "source-over";
    context.lineWidth = Math.max(1, stroke.radius * 2);
    const first = toCanvasPoint(stroke.points[0], canvas.width, canvas.height);
    context.beginPath();
    context.arc(first.x, first.y, stroke.radius, 0, Math.PI * 2);
    context.fill();
    if (stroke.points.length <= 1) continue;
    context.beginPath();
    context.moveTo(first.x, first.y);
    for (const point of stroke.points.slice(1)) {
      const next = toCanvasPoint(point, canvas.width, canvas.height);
      context.lineTo(next.x, next.y);
    }
    context.stroke();
  }
  context.globalCompositeOperation = "source-over";
}

function toCanvasPoint(point: LocalMaskPoint, width: number, height: number) {
  return {
    x: Math.min(width, Math.max(0, point.x * width)),
    y: Math.min(height, Math.max(0, point.y * height)),
  };
}

function clamp(value: number) {
  return Math.min(1, Math.max(0, value));
}

function stripExtension(fileName: string) {
  return fileName.replace(/\.[^.]+$/, "") || "front-reference";
}
