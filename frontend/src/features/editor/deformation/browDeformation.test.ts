import { describe, expect, it } from "vitest";
import { createBrowMeshTransforms, getBrowDisplacement, type BrowMeshTransform } from "./browDeformation";
import { animeFaceLandmarks, animeFaceSize } from "./fixtures/animeFace";
import { createDefaultLandmarks, resolveBrowLandmarks } from "./landmarks";
import { createEmptyRecipe, updateBrowControl, updateManualLandmark, type BrowControlKey } from "./recipe";

function brows(key: BrowControlKey, value: number) {
  const [left, right] = createBrowMeshTransforms(updateBrowControl(createEmptyRecipe(), key, value), animeFaceLandmarks);
  return { left, right };
}

function offsetAt(transform: BrowMeshTransform, point: { x: number; y: number }) {
  const scale = (p: { x: number; y: number }) => ({ x: p.x * animeFaceSize.width, y: p.y * animeFaceSize.height });
  const inner = scale(transform.inner), outer = scale(transform.outer), peak = scale(transform.peak), at = scale(point);
  return getBrowDisplacement(transform, {
    innerX: inner.x, innerY: inner.y, outerX: outer.x, outerY: outer.y, peakX: peak.x, peakY: peak.y, minHalfLength: 6,
  }, at.x, at.y);
}

const midpoint = (transform: BrowMeshTransform) => ({
  x: (transform.inner.x + transform.outer.x) / 2,
  y: (transform.inner.y + transform.outer.y) / 2,
});

describe("brow deformation", () => {
  it("does nothing until a brow control changes", () => {
    expect(createBrowMeshTransforms(createEmptyRecipe(), animeFaceLandmarks)).toEqual([]);
  });

  it("raises both brows for positive up/down", () => {
    const { left, right } = brows("browVertical", 0.5);

    expect(offsetAt(left, midpoint(left)).y).toBeLessThan(-3);
    expect(offsetAt(right, midpoint(right)).y).toBeLessThan(-3);
  });

  it("moves the brows apart, mostly at the inner end, for positive spacing", () => {
    const { left, right } = brows("browSpacing", 0.5);

    expect(offsetAt(left, left.inner).x).toBeLessThan(0);
    expect(offsetAt(right, right.inner).x).toBeGreaterThan(0);
    expect(Math.abs(offsetAt(left, left.outer).x)).toBeLessThan(Math.abs(offsetAt(left, left.inner).x));
  });

  it("moves only the brow heads for brow head spacing", () => {
    const { left } = brows("browHeadSpacing", 0.5);

    expect(offsetAt(left, left.inner).x).toBeLessThan(0);
    expect(offsetAt(left, left.outer).x).toBe(0);
  });

  it("extends the tail outward for positive length", () => {
    const { left, right } = brows("browLength", 0.5);

    expect(offsetAt(left, left.outer).x).toBeLessThan(0);
    expect(offsetAt(right, right.outer).x).toBeGreaterThan(0);
    expect(offsetAt(left, left.inner).x).toBe(0);
  });

  it("raises the brow heads for positive tilt", () => {
    const { left, right } = brows("browTilt", 0.5);

    expect(offsetAt(left, left.inner).y).toBeLessThan(0);
    expect(offsetAt(left, left.outer).y).toBeGreaterThan(0);
    expect(offsetAt(right, right.inner).y).toBeLessThan(0);
  });

  it("lifts the arch toward the tail and leaves the head in place for positive arch", () => {
    const { left } = brows("browPeak", 0.5);

    expect(offsetAt(left, left.peak).y).toBeLessThan(-3);
    expect(Math.abs(offsetAt(left, left.inner).y)).toBeLessThan(Math.abs(offsetAt(left, left.peak).y) * 0.1);
  });

  it("thickens the brow across its line for positive thickness", () => {
    const { left } = brows("browThickness", 0.5);
    const center = midpoint(left);
    const above = offsetAt(left, { x: center.x, y: center.y - 3 / animeFaceSize.height });
    const below = offsetAt(left, { x: center.x, y: center.y + 3 / animeFaceSize.height });

    expect(above.y).toBeLessThan(0);
    expect(below.y).toBeGreaterThan(0);
  });

  it("moves one brow point by dragging and keeps the other estimated points where they were shown", () => {
    const recipe = { ...createEmptyRecipe(), landmarks: createDefaultLandmarks(1, 1) };
    const estimated = resolveBrowLandmarks(recipe.landmarks);

    const moved = updateManualLandmark(recipe, "leftBrowPeak", { x: 0.4, y: 0.25 }).landmarks!;

    expect(moved.brows?.left.peak).toEqual({ x: 0.4, y: 0.25 });
    expect(moved.brows?.left.inner).toEqual(estimated.left.inner);
    expect(moved.brows?.right).toEqual(estimated.right);
    expect(moved.leftEye).toEqual(recipe.landmarks.leftEye);
  });

  it("estimates brows above the eyes when detection has none", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const estimated = resolveBrowLandmarks(landmarks);

    expect(estimated.left.inner.x).toBeGreaterThan(estimated.left.outer.x);
    expect(estimated.right.inner.x).toBeLessThan(estimated.right.outer.x);
    expect(estimated.left.inner.y).toBeLessThan(landmarks.leftEye.y);
  });
});
