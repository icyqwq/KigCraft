import type { LandmarkPoint, ManualLandmarks } from "./landmarks";

export type FaceBox = {
  height: number;
  width: number;
  x: number;
  y: number;
};

export type DetectedPoint = {
  score: number;
  x: number;
  y: number;
};

const heatmapSize = 64;
const animeLandmarkCount = 28;
const leftEyeContourIndexes = [11, 12, 13, 14, 15, 16] as const;
const rightEyeContourIndexes = [17, 18, 19, 20, 21, 22] as const;
const mouthContourIndexes = [24, 25, 26, 27] as const;
const controlLandmarkSourceIndexes = new Set([
  1,
  2,
  3,
  11,
  12,
  13,
  14,
  15,
  16,
  17,
  18,
  19,
  20,
  21,
  22,
  24,
  25,
  26,
  27,
]);

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function median(values: readonly number[]) {
  if (values.length === 0) return 0;

  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
}

function pickStableEyeCenterPoints(points: readonly DetectedPoint[]) {
  if (points.length <= 5) return points;

  const ys = points.map((point) => point.y);
  const medianY = median(ys);
  const deviations = ys.map((y) => Math.abs(y - medianY));
  const medianDeviation = median(deviations);
  const maxAllowedDeviation = Math.max(12, medianDeviation * 3.5);
  const filtered = points.filter((point) => Math.abs(point.y - medianY) <= maxAllowedDeviation);

  return filtered.length >= 4 ? filtered : points;
}

function deriveEyeCenter(points: readonly DetectedPoint[]) {
  if (points.length === 0) return { x: 0, y: 0 };

  const stablePoints = pickStableEyeCenterPoints(points);
  const xs = stablePoints.map((point) => point.x);
  const ys = stablePoints.map((point) => point.y);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);

  return {
    x: (minX + maxX) / 2,
    y: (minY + maxY) / 2,
  };
}

function deriveMouthPoints(
  points: readonly DetectedPoint[],
  leftEye: LandmarkPoint,
  rightEye: LandmarkPoint,
  chin: LandmarkPoint,
) {
  const mouthPoints = mouthContourIndexes.map((index) => points[index]).filter(Boolean);
  const eyeCenter = {
    x: (leftEye.x + rightEye.x) / 2,
    y: (leftEye.y + rightEye.y) / 2,
  };

  if (mouthPoints.length >= 2) {
    const sortedByX = [...mouthPoints].sort((left, right) => left.x - right.x);
    const left = sortedByX[0];
    const right = sortedByX[sortedByX.length - 1];
    const center = {
      x: mouthPoints.reduce((sum, point) => sum + point.x, 0) / mouthPoints.length,
      y: mouthPoints.reduce((sum, point) => sum + point.y, 0) / mouthPoints.length,
    };

    return { center, left, right };
  }

  const faceHeight = Math.max(1, Math.abs(chin.y - eyeCenter.y));
  const eyeDistance = Math.max(1, Math.abs(rightEye.x - leftEye.x));
  const center = {
    x: (eyeCenter.x + chin.x) / 2,
    y: eyeCenter.y + faceHeight * 0.52,
  };
  const halfWidth = eyeDistance * 0.18;

  return {
    center,
    left: { score: 0, x: center.x - halfWidth, y: center.y },
    right: { score: 0, x: center.x + halfWidth, y: center.y },
  };
}

function deriveEyePatchRegion(
  points: readonly DetectedPoint[],
  indexes: readonly number[],
  imageWidth: number,
  imageHeight: number,
  eyeDistance: number,
) {
  const eyePoints = indexes.map((index) => points[index]).filter(Boolean);
  const stablePoints = pickStableEyeCenterPoints(eyePoints);
  const center = deriveEyeCenter(eyePoints);
  const xs = eyePoints.map((point) => point.x);
  const ys = eyePoints.map((point) => point.y);
  const stableXs = stablePoints.map((point) => point.x);
  const stableYs = stablePoints.map((point) => point.y);
  const pointRadiusX = (Math.max(...stableXs) - Math.min(...stableXs)) * 0.68;
  const pointRadiusY = Math.max(
    center.y - Math.min(...ys),
    Math.max(...ys) - center.y,
    (Math.max(...stableYs) - Math.min(...stableYs)) * 0.72,
  );
  const radiusY = clamp(
    Math.max(pointRadiusY, eyeDistance * 0.13) / Math.max(1, imageHeight),
    0.028,
    0.13,
  );

  return {
    radiusX: Number(
      clamp(Math.max(pointRadiusX, eyeDistance * 0.22) / Math.max(1, imageWidth), 0.05, 0.17).toFixed(4),
    ),
    radiusBottomY: Number(radiusY.toFixed(4)),
    radiusTopY: Number(radiusY.toFixed(4)),
    radiusY: Number(radiusY.toFixed(4)),
  };
}

function normalizePoint(point: { x: number; y: number }, imageWidth: number, imageHeight: number) {
  const safeWidth = Math.max(1, imageWidth);
  const safeHeight = Math.max(1, imageHeight);

  return {
    x: Number(clamp(point.x / safeWidth, 0, 1).toFixed(4)),
    y: Number(clamp(point.y / safeHeight, 0, 1).toFixed(4)),
  };
}

export function expandFaceBox(
  box: FaceBox,
  imageWidth: number,
  imageHeight: number,
  padding = 1.18,
): FaceBox {
  const safePadding = Math.max(1, padding);
  const size = Math.max(box.width, box.height) * safePadding;
  const centerX = box.x + box.width / 2;
  const centerY = box.y + box.height / 2;

  return {
    height: size,
    width: size,
    x: clamp(centerX - size / 2, -size * 0.25, imageWidth - size * 0.75),
    y: clamp(centerY - size / 2, -size * 0.25, imageHeight - size * 0.75),
  };
}

export function decodeHrnetHeatmaps(data: Float32Array, box: FaceBox): DetectedPoint[] {
  const points: DetectedPoint[] = [];
  const planeSize = heatmapSize * heatmapSize;

  for (let joint = 0; joint < animeLandmarkCount; joint += 1) {
    let max = Number.NEGATIVE_INFINITY;
    let index = 0;
    const base = joint * planeSize;

    for (let offset = 0; offset < planeSize; offset += 1) {
      const value = data[base + offset];
      if (value > max) {
        max = value;
        index = offset;
      }
    }

    const peakX = index % heatmapSize;
    const peakY = Math.floor(index / heatmapSize);
    let x = peakX;
    let y = peakY;

    if (peakX > 0 && peakX < heatmapSize - 1) {
      x += Math.sign(data[base + peakY * heatmapSize + peakX + 1] - data[base + peakY * heatmapSize + peakX - 1]) * 0.25;
    }
    if (peakY > 0 && peakY < heatmapSize - 1) {
      y += Math.sign(data[base + (peakY + 1) * heatmapSize + peakX] - data[base + (peakY - 1) * heatmapSize + peakX]) * 0.25;
    }

    points.push({
      score: max,
      x: box.x + ((x + 0.5) / heatmapSize) * box.width,
      y: box.y + ((y + 0.5) / heatmapSize) * box.height,
    });
  }

  return points;
}

export function mapAnimePointsToManualLandmarks(
  points: readonly DetectedPoint[],
  imageWidth: number,
  imageHeight: number,
  _faceBox?: FaceBox,
): ManualLandmarks | null {
  if (points.length < animeLandmarkCount) return null;

  const leftEyePoints = leftEyeContourIndexes.map((index) => points[index]).filter(Boolean);
  const rightEyePoints = rightEyeContourIndexes.map((index) => points[index]).filter(Boolean);
  const leftEye = deriveEyeCenter(leftEyePoints);
  const rightEye = deriveEyeCenter(rightEyePoints);
  const eyeDistance = Math.max(1, Math.abs(rightEye.x - leftEye.x));
  const eyeRegions = {
    left: deriveEyePatchRegion(points, leftEyeContourIndexes, imageWidth, imageHeight, eyeDistance),
    right: deriveEyePatchRegion(points, rightEyeContourIndexes, imageWidth, imageHeight, eyeDistance),
  };

  const chin = points[2];
  const jawLeft = points[1];
  const jawRight = points[3];

  if (!chin || !jawLeft || !jawRight) return null;
  const mouth = deriveMouthPoints(points, leftEye, rightEye, chin);

  return {
    leftEye: normalizePoint(leftEye, imageWidth, imageHeight),
    rightEye: normalizePoint(rightEye, imageWidth, imageHeight),
    chin: normalizePoint(chin, imageWidth, imageHeight),
    jawLeft: normalizePoint(jawLeft, imageWidth, imageHeight),
    jawRight: normalizePoint(jawRight, imageWidth, imageHeight),
    mouthCenter: normalizePoint(mouth.center, imageWidth, imageHeight),
    mouthLeft: normalizePoint(mouth.left, imageWidth, imageHeight),
    mouthRight: normalizePoint(mouth.right, imageWidth, imageHeight),
    eyeRegions,
  };
}

export function normalizeAnimeDetailPoints(
  points: readonly DetectedPoint[],
  imageWidth: number,
  imageHeight: number,
): LandmarkPoint[] {
  if (points.length < animeLandmarkCount) return [];

  return points
    .map((point, index) => ({ index, point }))
    .filter(({ index }) => !controlLandmarkSourceIndexes.has(index))
    .map(({ point }) => normalizePoint(point, imageWidth, imageHeight));
}

