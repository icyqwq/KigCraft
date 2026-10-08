import { describe, expect, it } from "vitest";
import { createDefaultLandmarks, normalizeLandmarks } from "./landmarks";

describe("editor landmarks", () => {
  it("creates default face anchors from established editor proportions", () => {
    const width = 1000;
    const height = 800;
    const landmarks = createDefaultLandmarks(width, height);

    expect(landmarks.leftEye.x).toBeCloseTo(width * 0.42);
    expect(landmarks.leftEye.y).toBeCloseTo(height * 0.42);
    expect(landmarks.rightEye.x).toBeCloseTo(width * 0.58);
    expect(landmarks.rightEye.y).toBeCloseTo(height * 0.42);
    expect(landmarks.chin.x).toBeCloseTo(width * 0.5);
    expect(landmarks.chin.y).toBeCloseTo(height * 0.7);
    expect(landmarks.jawLeft.x).toBeCloseTo(width * 0.39);
    expect(landmarks.jawLeft.y).toBeCloseTo(height * 0.6);
    expect(landmarks.jawRight.x).toBeCloseTo(width * 0.61);
    expect(landmarks.jawRight.y).toBeCloseTo(height * 0.6);
  });

  it("preserves fractional default face anchors from established editor proportions", () => {
    const width = 101;
    const height = 103;
    const landmarks = createDefaultLandmarks(width, height);

    expect(landmarks.leftEye.x).toBeCloseTo(width * 0.42);
    expect(landmarks.leftEye.y).toBeCloseTo(height * 0.42);
    expect(landmarks.rightEye.x).toBeCloseTo(width * 0.58);
    expect(landmarks.rightEye.y).toBeCloseTo(height * 0.42);
    expect(landmarks.chin.x).toBeCloseTo(width * 0.5);
    expect(landmarks.chin.y).toBeCloseTo(height * 0.7);
    expect(landmarks.jawLeft.x).toBeCloseTo(width * 0.39);
    expect(landmarks.jawLeft.y).toBeCloseTo(height * 0.6);
    expect(landmarks.jawRight.x).toBeCloseTo(width * 0.61);
    expect(landmarks.jawRight.y).toBeCloseTo(height * 0.6);
  });

  it("normalizes manual landmarks into image-relative coordinates", () => {
    const normalized = normalizeLandmarks(createDefaultLandmarks(1000, 800), 1000, 800);

    expect(normalized.leftEye).toEqual({ x: 0.42, y: 0.42 });
    expect(normalized.rightEye).toEqual({ x: 0.58, y: 0.42 });
    expect(normalized.chin).toEqual({ x: 0.5, y: 0.7 });
    expect(normalized.jawLeft).toEqual({ x: 0.39, y: 0.6 });
    expect(normalized.jawRight).toEqual({ x: 0.61, y: 0.6 });
  });
});
