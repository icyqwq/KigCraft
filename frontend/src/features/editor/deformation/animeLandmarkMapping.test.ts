import { describe, expect, it } from "vitest";
import {
  decodeHrnetHeatmaps,
  expandFaceBox,
  mapAnimePointsToManualLandmarks,
  normalizeAnimeDetailPoints,
  type DetectedPoint,
} from "./animeLandmarkMapping";

describe("anime landmark mapping", () => {
  it("expands MediaPipe face boxes into a square HRNet crop", () => {
    const box = expandFaceBox({ height: 120, width: 180, x: 300, y: 260 }, 900, 700, 1.3);

    expect(box.width).toBeCloseTo(234);
    expect(box.height).toBeCloseTo(234);
    expect(box.x).toBeCloseTo(273);
    expect(box.y).toBeCloseTo(203);
  });

  it("decodes 28 HRNet heatmaps back into image-space points", () => {
    const heatmaps = new Float32Array(28 * 64 * 64);
    for (let joint = 0; joint < 28; joint += 1) {
      heatmaps[joint * 64 * 64 + 20 * 64 + 30] = 1;
    }

    const points = decodeHrnetHeatmaps(heatmaps, { height: 256, width: 256, x: 100, y: 50 });

    expect(points).toHaveLength(28);
    expect(points[0].x).toBeCloseTo(222);
    expect(points[0].y).toBeCloseTo(132);
  });

  it("maps anime detector points into editor landmarks", () => {
    const points: DetectedPoint[] = Array.from({ length: 28 }, (_, index) => ({
      score: 1,
      x: 100 + index * 10,
      y: 200 + index * 5,
    }));

    const landmarks = mapAnimePointsToManualLandmarks(points, 1000, 800);

    expect(landmarks).toEqual({
      leftEye: { x: 0.235, y: 0.3344 },
      rightEye: { x: 0.295, y: 0.3719 },
      chin: { x: 0.12, y: 0.2625 },
      jawLeft: { x: 0.11, y: 0.2562 },
      jawRight: { x: 0.13, y: 0.2687 },
      mouthCenter: { x: 0.355, y: 0.4094 },
      mouthLeft: { x: 0.34, y: 0.4 },
      mouthRight: { x: 0.37, y: 0.4188 },
      eyeRegions: {
        left: { radiusBottomY: 0.028, radiusTopY: 0.028, radiusX: 0.05, radiusY: 0.028 },
        right: { radiusBottomY: 0.028, radiusTopY: 0.028, radiusX: 0.05, radiusY: 0.028 },
      },
    });
  });

  it("uses HRNet jaw and chin points instead of synthetic MediaPipe face-box controls", () => {
    const points: DetectedPoint[] = Array.from({ length: 28 }, (_, index) => ({
      score: 1,
      x: 200 + index * 4,
      y: 120 + index * 20,
    }));

    const landmarks = mapAnimePointsToManualLandmarks(points, 1000, 1000, {
      height: 500,
      width: 420,
      x: 280,
      y: 180,
    });

    expect(landmarks).toEqual({
      leftEye: { x: 0.254, y: 0.39 },
      rightEye: { x: 0.278, y: 0.51 },
      jawLeft: { x: 0.204, y: 0.14 },
      jawRight: { x: 0.212, y: 0.18 },
      chin: { x: 0.208, y: 0.16 },
      mouthCenter: { x: 0.302, y: 0.63 },
      mouthLeft: { x: 0.296, y: 0.6 },
      mouthRight: { x: 0.308, y: 0.66 },
      eyeRegions: {
        left: { radiusBottomY: 0.072, radiusTopY: 0.072, radiusX: 0.05, radiusY: 0.072 },
        right: { radiusBottomY: 0.072, radiusTopY: 0.072, radiusX: 0.05, radiusY: 0.072 },
      },
    });
  });

  it("derives wider eye patch regions from reliable HRNet eye contour points", () => {
    const points: DetectedPoint[] = Array.from({ length: 28 }, (_, index) => ({
      score: 1,
      x: 500,
      y: 500,
    }));
    [11, 12, 13, 14, 15, 16].forEach((index, offset) => {
      points[index] = {
        score: 1,
        x: 340 + offset * 28,
        y: 360 + (offset % 2) * 60,
      };
    });
    [17, 18, 19, 20, 21, 22].forEach((index, offset) => {
      points[index] = {
        score: 1,
        x: 560 + offset * 28,
        y: 360 + (offset % 2) * 60,
      };
    });

    const landmarks = mapAnimePointsToManualLandmarks(points, 1000, 1000);

    expect(landmarks?.eyeRegions?.left.radiusX).toBeGreaterThan(0.05);
    expect(landmarks?.eyeRegions?.left.radiusY).toBeGreaterThan(0.035);
    expect(landmarks?.eyeRegions?.left.radiusTopY).toBe(landmarks?.eyeRegions?.left.radiusBottomY);
    expect(landmarks?.eyeRegions?.right).toEqual(landmarks?.eyeRegions?.left);
  });

  it("uses the geometric center of five stable eye points for each eye anchor", () => {
    const points: DetectedPoint[] = Array.from({ length: 28 }, () => ({
      score: 1,
      x: 500,
      y: 500,
    }));
    const leftEye = [
      [100, 100],
      [140, 100],
      [180, 115],
      [100, 160],
      [140, 420],
      [180, 160],
    ];
    const rightEye = [
      [620, 180],
      [660, 180],
      [700, 195],
      [620, 240],
      [660, 460],
      [700, 240],
    ];

    [11, 12, 13, 14, 15, 16].forEach((index, offset) => {
      points[index] = { score: 1, x: leftEye[offset][0], y: leftEye[offset][1] };
    });
    [17, 18, 19, 20, 21, 22].forEach((index, offset) => {
      points[index] = { score: 1, x: rightEye[offset][0], y: rightEye[offset][1] };
    });

    const landmarks = mapAnimePointsToManualLandmarks(points, 1000, 1000);

    expect(landmarks?.leftEye).toEqual({ x: 0.14, y: 0.13 });
    expect(landmarks?.rightEye).toEqual({ x: 0.66, y: 0.21 });
  });

  it("derives centered elliptical eye regions instead of offset circular regions", () => {
    const points: DetectedPoint[] = Array.from({ length: 28 }, () => ({
      score: 1,
      x: 500,
      y: 500,
    }));
    [11, 12, 13, 14, 15, 16].forEach((index, offset) => {
      points[index] = {
        score: 1,
        x: 300 + offset * 24,
        y: 360 + (offset % 2) * 22,
      };
    });
    [17, 18, 19, 20, 21, 22].forEach((index, offset) => {
      points[index] = {
        score: 1,
        x: 560 + offset * 24,
        y: 360 + (offset % 2) * 22,
      };
    });

    const landmarks = mapAnimePointsToManualLandmarks(points, 1000, 1000);
    const leftRegion = landmarks?.eyeRegions?.left;

    expect(leftRegion?.radiusX).toBeGreaterThan(leftRegion?.radiusY ?? 0);
    expect(leftRegion?.radiusTopY).toBe(leftRegion?.radiusBottomY);
  });

  it("keeps non-control HRNet points as secondary debug landmarks", () => {
    const points: DetectedPoint[] = Array.from({ length: 28 }, (_, index) => ({
      score: 1,
      x: 100 + index * 10,
      y: 200 + index * 5,
    }));

    const details = normalizeAnimeDetailPoints(points, 1000, 800);

    expect(details).toHaveLength(9);
    expect(details[0]).toEqual({ x: 0.1, y: 0.25 });
    expect(details.some((point) => point.x === 0.11)).toBe(false);
    expect(details.some((point) => point.x === 0.22)).toBe(false);
  });
});

