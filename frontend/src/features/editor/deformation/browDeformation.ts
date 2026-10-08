import { flatFalloff, peakedFalloff } from "./falloff";
import { resolveBrowLandmarks, type BrowLandmark, type ManualLandmarks } from "./landmarks";
import type { EditRecipe } from "./recipe";

/**
 * One brow's deformation, in normalized image coordinates for the brow line and in units of half the
 * brow length for amplitudes. Signs follow the reference retouching app at its slider maximum.
 */
export type BrowMeshTransform = {
  inner: { x: number; y: number };
  outer: { x: number; y: number };
  peak: { x: number; y: number };
  /** Sign of the image X direction pointing away from the other brow. */
  outwardSign: -1 | 1;
  /** Positive raises the brow. */
  vertical: number;
  /** Factor offset across the brow line; positive thickens. */
  thickness: number;
  /** Positive extends the tail. */
  length: number;
  /** Positive moves the brows apart, mostly at the inner end. */
  spacing: number;
  /** Positive moves only the brow heads apart. */
  headSpacing: number;
  /** Radians in image coordinates; the sign is chosen so positive control values raise the brow heads. */
  rotation: number;
  /** Positive raises the arch toward the tail. */
  peakLift: number;
};

// Amplitudes at the stored control limits, in half-brow-lengths. The editor's default slider covers half.
const browVerticalAmplitude = 0.36;
const browThicknessGrow = 0.9;
const browThicknessShrink = 0.6;
const browLengthAmplitude = 0.4;
const browSpacingAmplitude = 0.26;
const browHeadSpacingAmplitude = 0.32;
const maxBrowTiltRadians = 0.24;
const browPeakAmplitude = 0.42;

// Supports, in half-brow-lengths along the brow and across it.
const browMoveAlongSupport = 1.5;
const browMoveAcrossSupport = 1.25;
const browSpacingSupport = 2.6;
const browEndSupport = 1;
const browRotationAlongSupport = 1.45;
const browRotationAcrossSupport = 1.2;
const browPeakAlongSupport = 1.4;
const browPeakAcrossSupport = 1.15;
const browThicknessAlongSupport = 1.25;
const browThicknessAcrossSupport = 0.45;
/** Furthest reach of any brow deformation, used to skip vertices early. */
export const browMaxSupport = Math.max(browMoveAlongSupport, 1 + browSpacingSupport, browRotationAlongSupport) + 0.1;

function clampUnit(value: number) {
  return Math.min(1, Math.max(-1, value));
}

function round(value: number) {
  return Number(value.toFixed(4));
}

export function createBrowMeshTransforms(recipe: EditRecipe, landmarks: ManualLandmarks): BrowMeshTransform[] {
  const { brows } = recipe;
  const values = {
    vertical: round(clampUnit(brows.browVertical) * browVerticalAmplitude),
    thickness: round(brows.browThickness >= 0
      ? clampUnit(brows.browThickness) * browThicknessGrow
      : clampUnit(brows.browThickness) * browThicknessShrink),
    length: round(clampUnit(brows.browLength) * browLengthAmplitude),
    spacing: round(clampUnit(brows.browSpacing) * browSpacingAmplitude),
    headSpacing: round(clampUnit(brows.browHeadSpacing) * browHeadSpacingAmplitude),
    tilt: round(clampUnit(brows.browTilt) * maxBrowTiltRadians),
    peakLift: round(clampUnit(brows.browPeak) * browPeakAmplitude),
  };
  if (Object.values(values).every((value) => Math.abs(value) < 0.001)) return [];

  const resolved = resolveBrowLandmarks(landmarks);
  const create = (brow: BrowLandmark, other: BrowLandmark): BrowMeshTransform => {
    const center = (brow.inner.x + brow.outer.x) / 2;
    const otherCenter = (other.inner.x + other.outer.x) / 2;
    const outwardSign: -1 | 1 = center <= otherCenter ? -1 : 1;
    const { tilt, ...rest } = values;

    return {
      inner: brow.inner,
      outer: brow.outer,
      peak: brow.peak,
      outwardSign,
      ...rest,
      // Raising the head turns the left brow counterclockwise on screen and the right one clockwise.
      rotation: round(outwardSign * tilt),
    };
  };

  return [create(resolved.left, resolved.right), create(resolved.right, resolved.left)];
}

/** Brow line in the pixel unit of the displacement, for one brow. */
export type BrowPixelGeometry = {
  innerX: number;
  innerY: number;
  outerX: number;
  outerY: number;
  peakX: number;
  peakY: number;
  minHalfLength: number;
};

/** Displacement of the point (x, y), in the pixel unit of the geometry. Positive Y is down. */
export function getBrowDisplacement(transform: BrowMeshTransform, geometry: BrowPixelGeometry, x: number, y: number) {
  const centerX = (geometry.innerX + geometry.outerX) / 2;
  const centerY = (geometry.innerY + geometry.outerY) / 2;
  const axisLength = Math.hypot(geometry.outerX - geometry.innerX, geometry.outerY - geometry.innerY);
  const halfLength = Math.max(geometry.minHalfLength, axisLength / 2);
  // Along: inner end to tail. Across: perpendicular, pointing down the image.
  const alongX = axisLength > 0 ? (geometry.outerX - geometry.innerX) / axisLength : transform.outwardSign;
  const alongY = axisLength > 0 ? (geometry.outerY - geometry.innerY) / axisLength : 0;
  const acrossSign = alongX >= 0 ? 1 : -1;
  const acrossX = -alongY * acrossSign;
  const acrossY = alongX * acrossSign;
  const localX = x - centerX;
  const localY = y - centerY;
  const along = (localX * alongX + localY * alongY) / halfLength;
  const across = (localX * acrossX + localY * acrossY) / halfLength;
  const peakAlong = clampPeak(((geometry.peakX - centerX) * alongX + (geometry.peakY - centerY) * alongY) / halfLength);
  let offsetX = 0;
  let offsetY = 0;

  const moveWeight = flatFalloff(Math.abs(along) / browMoveAlongSupport) * flatFalloff(Math.abs(across) / browMoveAcrossSupport);
  offsetY -= transform.vertical * halfLength * moveWeight;

  // Spacing follows the reference: the inner end moves fully and the tail stays nearly in place.
  const acrossWeight = flatFalloff(Math.abs(across) / browMoveAcrossSupport);
  const spacingWeight = peakedFalloff(Math.abs(along + 1) / browSpacingSupport) * acrossWeight;
  offsetX += transform.outwardSign * transform.spacing * halfLength * spacingWeight;

  const headWeight = peakedFalloff(Math.hypot(along + 1, across) / browEndSupport);
  offsetX += transform.outwardSign * transform.headSpacing * halfLength * headWeight;

  const tailWeight = peakedFalloff(Math.hypot(along - 1, across) / browEndSupport);
  offsetX += alongX * transform.length * halfLength * tailWeight;
  offsetY += alongY * transform.length * halfLength * tailWeight;

  if (transform.rotation !== 0) {
    const rotationWeight = flatFalloff(Math.hypot(along / browRotationAlongSupport, across / browRotationAcrossSupport));
    const cos = Math.cos(transform.rotation);
    const sin = Math.sin(transform.rotation);
    offsetX += (localX * cos - localY * sin - localX) * rotationWeight;
    offsetY += (localX * sin + localY * cos - localY) * rotationWeight;
  }

  const peakWeight = peakedFalloff(Math.hypot((along - peakAlong) / browPeakAlongSupport, across / browPeakAcrossSupport));
  offsetY -= transform.peakLift * halfLength * peakWeight;

  const thicknessWeight = flatFalloff(Math.abs(along) / browThicknessAlongSupport) *
    peakedFalloff(Math.abs(across) / browThicknessAcrossSupport);
  offsetX += acrossX * across * halfLength * transform.thickness * thicknessWeight;
  offsetY += acrossY * across * halfLength * transform.thickness * thicknessWeight;

  return { x: offsetX, y: offsetY };
}

/** Anime brows arch nearer the tail; keep the arch between the brow center and the tail. */
function clampPeak(value: number) {
  return Math.min(0.6, Math.max(0.2, value));
}
