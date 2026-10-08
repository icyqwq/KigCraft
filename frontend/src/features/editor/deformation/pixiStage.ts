import { validImageCoordinates } from "../core/imageCoordinates";
import { browMaxSupport, createBrowMeshTransforms, getBrowDisplacement, type BrowMeshTransform } from "./browDeformation";
import {
  createFaceShapeDeformer,
  createFaceShapeTransform,
  createFeatureProtectionZones,
  createProportionStrokes,
  getFeatureProtection,
  type FaceShapeTransform,
  type FeatureProtectionZone,
} from "./faceDeformation";
import { flatFalloff, peakedFalloff, peakedRamp } from "./falloff";
import { createDefaultLandmarks, type ManualLandmarks } from "./landmarks";
import { eyeControlRanges, mouthControlRanges, type EditRecipe, type LiquifyMode, type LiquifyStroke } from "./recipe";

type PixiModule = typeof import("pixi.js");

export type PixiStageHandle = {
  setImageUrl(url: string): Promise<void>;
  applyRecipe(recipe: EditRecipe): void;
  exportImage(): Promise<Blob>;
  destroy(): void;
};

/**
 * One eye's deformation. Every field is relative to the eye selection, so the result does not
 * depend on the viewport. Shifts and lid moves keep the selection boundary fixed and only move
 * the content inside it, instead of moving the whole eye patch rigidly.
 */
export type EyeMeshTransform = {
  centerX: number;
  centerY: number;
  /** Sign of the image X direction pointing away from the other eye. */
  outwardSign: -1 | 1;
  radiusBottomY: number;
  radiusX: number;
  radiusTopY: number;
  radiusY: number;
  /** Bulge for eye size and eye height; 1 is unchanged. */
  scaleX: number;
  scaleY: number;
  /** Horizontal stretch inside the eye only, as a factor offset; 0 is unchanged. */
  stretchX: number;
  /** Interior shift as a fraction of the selection radius. Positive X is outward, positive Y is down. */
  shiftX: number;
  shiftY: number;
  rotation: number;
  /** Iris magnification at the eye center, as a factor offset. */
  irisScale: number;
  /** Outer corner lift as a fraction of the vertical selection radius. Positive is up. */
  tailLift: number;
  /** Upper lid lift and lower lid drop as fractions of the vertical selection radius. */
  upperLid: number;
  lowerLid: number;
};

export type MouthMeshTransform = {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  scaleX: number;
  scaleY: number;
  /** -1 to 1; positive lifts the corners. */
  smile: number;
  /** Fractions of the mouth patch radius on each axis. */
  translateX: number;
  translateY: number;
};

export type RecipePreview = {
  allLiquifyStrokes: LiquifyStroke[];
  browMeshTransforms: BrowMeshTransform[];
  displacementScale: { x: number; y: number };
  detailRegionCount: number;
  eyeMeshTransforms: EyeMeshTransform[];
  eyeOffset: { x: number; y: number };
  eyeScale: { x: number; y: number };
  eyeSkew: number;
  eyeTransformCount: number;
  faceShapeTransform: FaceShapeTransform | null;
  /** Proportion strokes; face-shape edits are in faceShapeTransform. */
  featureLiquifyStrokes: LiquifyStroke[];
  featureProtectionZones: FeatureProtectionZone[];
  featureStrokeCount: number;
  imageOffset: { x: number; y: number };
  imageScale: { x: number; y: number };
  imageSkew: { x: number; y: number };
  jawScale: { x: number; y: number };
  liquifyIntensity: number;
  manualStrokeCount: number;
  mouthMeshTransforms: MouthMeshTransform[];
  mouthTransformCount: number;
  strokeCount: number;
};

export type RecipePreviewOptions = {
  landmarks?: ManualLandmarks;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function round(value: number) {
  return Number(value.toFixed(4));
}

function canvasToBlob(canvas: import("pixi.js").ICanvas): Promise<Blob> {
  if (canvas.convertToBlob) return canvas.convertToBlob({ type: "image/png" });
  if (!canvas.toBlob) return Promise.reject(new Error("Canvas PNG export is unavailable"));
  return new Promise((resolve, reject) => {
    canvas.toBlob!((blob) => {
      if (blob) {
        resolve(blob);
        return;
      }
      reject(new Error("Unable to export Pixi canvas"));
    }, "image/png");
  });
}

const displacementTextureSize = 256;
const canonicalPreviewSize = 720;
const maxLocalDisplacementScale = 96;
// Dense enough that eye and iris warps, a few percent of the image wide, stay smooth.
const meshVerticesX = 129;
const meshVerticesY = 177;
const maxMeshDisplacementCssPixels = 84;
const maxAccumulatedMeshDisplacementCssPixels = 128;
const maxEyeDistanceControlValue = eyeControlRanges.eyeDistance.max;
const maxEyeVerticalControlValue = eyeControlRanges.eyeVertical.max;
const maxEyeTiltRadians = 0.07;
// Supports are multiples of the eye selection radius. Shapes and strengths follow the optical flow
// measured on the reference retouching app at its slider ends, which match half of our stored range.
const eyeScaleSupport = 1.3;
const eyeRotationSupport = 1.2;
const eyeIrisSupport = 0.9;
// Up/down and distance move the whole eye with its lashes. The inner corner follows; the outer corner
// stays almost anchored; the skin above and below absorbs the move.
const eyeShiftInnerSupport = 1.25;
const eyeShiftOuterSupport = 0.65;
const eyeShiftTopSupport = 1.35;
const eyeShiftBottomSupport = 1.2;
// Outer corner lift is a bump around the outer corner that leaves the eye center in place.
const eyeTailCornerX = 0.56;
const eyeTailSupportX = 0.34;
const eyeTailSupportY = 1;
const eyeLidSupportX = 1.15;
const eyeLidSupportY = 1.6;
const eyeMaxSupportX = Math.max(eyeScaleSupport, eyeShiftInnerSupport, eyeTailCornerX + eyeTailSupportX, eyeLidSupportX);
const eyeMaxSupportY = Math.max(eyeScaleSupport, eyeShiftTopSupport, eyeLidSupportY);
// Amplitudes at the stored control limits, kept below the mesh fold-over limit of each profile.
const maxEyeBulge = 0.54;
const eyeShiftXAmplitude = 0.15;
const eyeShiftYAmplitude = 0.26;
const eyeStretchAmplitude = 0.55;
const eyeIrisAmplitude = 0.5;
const eyeTailLiftAmplitude = 0.48;
const eyeLidAmplitude = 0.34;
const defaultEyePatchRadiusX = 0.12;
const defaultEyePatchRadiusY = 0.085;
const defaultEyePatchRadiusTopY = 0.12;
const defaultEyePatchRadiusBottomY = 0.075;
const maxMouthHorizontalControlValue = mouthControlRanges.mouthHorizontal.max;
const maxMouthVerticalControlValue = mouthControlRanges.mouthVertical.max;
const maxMouthSmileControlValue = mouthControlRanges.mouthSmile.max;
// Mouth moves are fractions of the mouth patch radius at the stored control limits, below fold-over.
const mouthTranslateXAmplitude = 0.12;
const mouthTranslateYAmplitude = 0.2;
const mouthSmileAmplitude = 0.32;
const defaultMouthPatchRadiusX = 0.08;
const defaultMouthPatchRadiusY = 0.045;
/** How far toward the jaw line and chin the mouth patch may reach, as a share of the distance to them. */
const mouthPatchFaceShare = 0.7;

function getEyeRegionScaleMultiplier(value: number) {
  return round(clamp(1 + value / 100, 0.5, 1.4));
}

function scaleEyeRegionRadius(value: number, multiplier: number) {
  return round(value * multiplier);
}

export function getDisplacementTextureRadius(radius: number) {
  return round(clamp(radius / canonicalPreviewSize * displacementTextureSize, 4, 60));
}

export function calculateDisplacementPixel(
  strokes: readonly LiquifyStroke[],
  point: { x: number; y: number },
  textureSize = displacementTextureSize,
) {
  let redOffset = 0;
  let greenOffset = 0;
  const pixelX = clamp(point.x, 0, 1) * textureSize;
  const pixelY = clamp(point.y, 0, 1) * textureSize;

  for (const stroke of strokes) {
    const centerX = clamp(stroke.x, 0, 1) * textureSize;
    const centerY = clamp(stroke.y, 0, 1) * textureSize;
    const radius = getDisplacementTextureRadius(stroke.radius);
    const deltaX = pixelX - centerX;
    const deltaY = pixelY - centerY;
    const distance = Math.hypot(deltaX, deltaY);
    if (distance > radius) continue;

    const falloff = (1 - distance / radius) ** 2 * clamp(stroke.strength, 0, 1);
    const vector = getStrokeVector(stroke, deltaX, deltaY, distance, radius);

    redOffset += vector.x * falloff * 124;
    greenOffset += vector.y * falloff * 124;
  }

  return {
    blue: 128,
    green: Math.round(clamp(128 + greenOffset, 0, 255)),
    red: Math.round(clamp(128 + redOffset, 0, 255)),
  };
}

function modeVector(mode: LiquifyMode) {
  switch (mode) {
    case "expand":
      return { x: 0, y: -0.7 };
    case "shrink":
      return { x: 0, y: 0.7 };
    case "push-left":
      return { x: -1, y: 0 };
    case "push-right":
      return { x: 1, y: 0 };
    case "push-up":
      return { x: 0, y: -1 };
    case "push-down":
      return { x: 0, y: 1 };
    default:
      return { x: 0, y: 0 };
  }
}

function getStrokeAxisWeight(stroke: LiquifyStroke) {
  const weight = Math.abs(clamp(stroke.strength, -1, 1)) * clamp(stroke.radius, 0, 240) / 120;

  if (stroke.mode === "expand" || stroke.mode === "shrink") {
    return { x: weight, y: weight, intensity: weight };
  }

  if (stroke.mode === "warp") {
    const vector = getWarpStrokeVector(stroke);
    const intensity = clamp(Math.hypot(stroke.deltaX ?? 0, stroke.deltaY ?? 0) / 0.12, 0, 1) * weight;

    return {
      x: Math.abs(vector.x) * weight,
      y: Math.abs(vector.y) * weight,
      intensity,
    };
  }

  if (stroke.mode === "scale") {
    const scaleWeight = Math.abs(clamp(stroke.scale ?? 0, -1, 1)) * clamp(stroke.radius, 0, 240) / 120;

    return { x: scaleWeight, y: scaleWeight, intensity: scaleWeight };
  }

  const vector = modeVector(stroke.mode);

  return {
    x: Math.abs(vector.x * weight),
    y: Math.abs(vector.y * weight),
    intensity: weight,
  };
}

function scaleAxisWeight(axisWeight: number) {
  if (axisWeight <= 0) return 0;

  return round(clamp(axisWeight * 112, 0, maxLocalDisplacementScale));
}

function resizePixiAppToHost(
  app: {
    resize?: () => void;
    renderer: { resize?: (width: number, height: number) => void };
  },
  host: HTMLDivElement,
) {
  const width = Math.max(1, Math.round(host.clientWidth || host.getBoundingClientRect().width || 1));
  const height = Math.max(1, Math.round(host.clientHeight || host.getBoundingClientRect().height || 1));

  app.resize?.();
  app.renderer.resize?.(width, height);
}

export function calculateRecipePreview(recipe: EditRecipe, options: RecipePreviewOptions = {}): RecipePreview {
  const landmarks = options.landmarks ?? recipe.landmarks ?? createDefaultLandmarks(1, 1);
  const faceShapeTransform = createFaceShapeTransform(recipe, landmarks);
  const featureLiquifyStrokes = createProportionStrokes(recipe, landmarks);
  const featureProtectionZones = faceShapeTransform ? createFeatureProtectionZones(landmarks) : [];
  const eyeMeshTransforms = createEyeMeshTransforms(recipe, landmarks);
  const browMeshTransforms = createBrowMeshTransforms(recipe, landmarks);
  const mouthMeshTransforms = createMouthMeshTransforms(recipe, landmarks);
  const allLiquifyStrokes = [...featureLiquifyStrokes, ...recipe.liquify];
  const liquifyVector = allLiquifyStrokes.reduce(
    (accumulator, stroke) => {
      const axisWeight = getStrokeAxisWeight(stroke);

      return {
        x: Math.max(accumulator.x, axisWeight.x),
        y: Math.max(accumulator.y, axisWeight.y),
        intensity: Math.max(accumulator.intensity, axisWeight.intensity),
      };
    },
    { x: 0, y: 0, intensity: 0 },
  );
  const eyeTransformVector = eyeMeshTransforms.reduce(
    (accumulator, transform) => {
      const scaleX = Math.max(Math.abs(transform.scaleX - 1), Math.abs(transform.stretchX), Math.abs(transform.irisScale));
      const scaleY = Math.max(Math.abs(transform.scaleY - 1), Math.abs(transform.irisScale));
      const rotation = Math.abs(transform.rotation);
      const shiftX = Math.abs(transform.shiftX);
      const shiftY = Math.max(
        Math.abs(transform.shiftY),
        Math.abs(transform.tailLift),
        Math.abs(transform.upperLid),
        Math.abs(transform.lowerLid),
      );

      return {
        x: Math.max(accumulator.x, shiftX, scaleX * 1.2, rotation * 1.4),
        y: Math.max(accumulator.y, shiftY, scaleY * 1.2, rotation * 1.4),
        intensity: Math.max(accumulator.intensity, shiftX, shiftY, scaleX, scaleY, rotation),
      };
    },
    { x: 0, y: 0, intensity: 0 },
  );
  // Face-shape profiles are fractions of the face width; about 5% is a strong edit.
  const faceShapeVector = (faceShapeTransform?.bands ?? []).reduce(
    (accumulator, band) => {
      const x = Math.max(...band.inward.map(Math.abs)) * 20;
      const y = Math.max(...band.up.map(Math.abs)) * 20;
      return { x: Math.max(accumulator.x, x), y: Math.max(accumulator.y, y), intensity: Math.max(accumulator.intensity, x, y) };
    },
    { x: 0, y: 0, intensity: 0 },
  );
  const browTransformVector = browMeshTransforms.reduce(
    (accumulator, transform) => {
      const x = Math.max(Math.abs(transform.spacing), Math.abs(transform.headSpacing), Math.abs(transform.length));
      const y = Math.max(Math.abs(transform.vertical), Math.abs(transform.peakLift), Math.abs(transform.thickness));
      const rotation = Math.abs(transform.rotation);

      return {
        x: Math.max(accumulator.x, x, rotation),
        y: Math.max(accumulator.y, y, rotation),
        intensity: Math.max(accumulator.intensity, x, y, rotation),
      };
    },
    { x: 0, y: 0, intensity: 0 },
  );
  const mouthTransformVector = mouthMeshTransforms.reduce(
    (accumulator, transform) => {
      const scaleX = Math.abs(transform.scaleX - 1);
      const scaleY = Math.abs(transform.scaleY - 1);
      const translateX = Math.abs(transform.translateX) / mouthTranslateXAmplitude;
      const translateY = Math.abs(transform.translateY) / mouthTranslateYAmplitude;
      const smile = Math.abs(transform.smile);

      return {
        x: Math.max(accumulator.x, translateX, scaleX, smile),
        y: Math.max(accumulator.y, translateY, scaleY, smile),
        intensity: Math.max(accumulator.intensity, translateX, translateY, scaleX, scaleY, smile),
      };
    },
    { x: 0, y: 0, intensity: 0 },
  );

  return {
    allLiquifyStrokes,
    browMeshTransforms,
    detailRegionCount: 0,
    displacementScale: {
      x: scaleAxisWeight(Math.max(liquifyVector.x, faceShapeVector.x, eyeTransformVector.x, browTransformVector.x, mouthTransformVector.x)),
      y: scaleAxisWeight(Math.max(liquifyVector.y, faceShapeVector.y, eyeTransformVector.y, browTransformVector.y, mouthTransformVector.y)),
    },
    eyeMeshTransforms,
    eyeOffset: { x: 0, y: 0 },
    eyeScale: { x: 1, y: 1 },
    eyeSkew: 0,
    eyeTransformCount: eyeMeshTransforms.length,
    faceShapeTransform,
    featureLiquifyStrokes,
    featureProtectionZones,
    featureStrokeCount: featureLiquifyStrokes.length,
    imageOffset: { x: 0, y: 0 },
    imageScale: { x: 1, y: 1 },
    imageSkew: { x: 0, y: 0 },
    jawScale: { x: 1, y: 1 },
    liquifyIntensity: round(Math.max(
      liquifyVector.intensity,
      faceShapeVector.intensity,
      eyeTransformVector.intensity,
      browTransformVector.intensity,
      mouthTransformVector.intensity,
    )),
    manualStrokeCount: recipe.liquify.length,
    mouthMeshTransforms,
    mouthTransformCount: mouthMeshTransforms.length,
    strokeCount: allLiquifyStrokes.length,
  };
}

/** Proportion strokes; face-shape edits deform along the outline instead, see createFaceShapeTransform. */
export function createFeatureLiquifyStrokes(recipe: EditRecipe, landmarks: ManualLandmarks): LiquifyStroke[] {
  return createProportionStrokes(recipe, landmarks);
}

export function createEyeMeshTransforms(recipe: EditRecipe, landmarks: ManualLandmarks): EyeMeshTransform[] {
  const { eyes } = recipe;
  const eyeRegionScale = getEyeRegionScaleMultiplier(eyes.eyeRegionScale);
  const scaleX = round(1 + clamp(eyes.eyeSize * 0.45, -0.5, maxEyeBulge));
  const scaleY = round(1 + clamp(eyes.eyeSize * 0.45 + eyes.eyeHeight * 0.4, -0.5, maxEyeBulge));
  const stretchX = round(clamp(eyes.eyeWidth, -1, 1) * eyeStretchAmplitude);
  const shiftX = round(clamp(eyes.eyeDistance / maxEyeDistanceControlValue, -1, 1) * eyeShiftXAmplitude);
  const shiftY = round(clamp(eyes.eyeVertical / maxEyeVerticalControlValue, -1, 1) * eyeShiftYAmplitude);
  const rotationMagnitude = round(clamp(eyes.eyeTilt, -1, 1) * maxEyeTiltRadians);
  const irisScale = round(clamp(eyes.eyeIrisSize, -1, 1) * eyeIrisAmplitude);
  const tailLift = round(clamp(eyes.eyeTailLift, -1, 1) * eyeTailLiftAmplitude);
  const upperLid = round(clamp(eyes.eyeUpperLid, -1, 1) * eyeLidAmplitude);
  const lowerLid = round(clamp(eyes.eyeLowerLid, -1, 1) * eyeLidAmplitude);
  const hasTransform = [scaleX - 1, scaleY - 1, stretchX, shiftX, shiftY, rotationMagnitude, irisScale, tailLift, upperLid, lowerLid]
    .some((value) => Math.abs(value) >= 0.001);

  if (!hasTransform) return [];

  const createTransform = (side: "left" | "right"): EyeMeshTransform => {
    const center = side === "left" ? landmarks.leftEye : landmarks.rightEye;
    const otherCenter = side === "left" ? landmarks.rightEye : landmarks.leftEye;
    const region = landmarks.eyeRegions?.[side];
    // Outward points away from the other eye, so swapped landmarks still lift the outer corner.
    const outwardSign: -1 | 1 = center.x < otherCenter.x || (center.x === otherCenter.x && side === "left") ? -1 : 1;

    return {
      centerX: center.x,
      centerY: center.y,
      outwardSign,
      radiusBottomY: scaleEyeRegionRadius(region?.radiusBottomY ?? region?.radiusY ?? defaultEyePatchRadiusBottomY, eyeRegionScale),
      radiusX: scaleEyeRegionRadius(region?.radiusX ?? defaultEyePatchRadiusX, eyeRegionScale),
      radiusTopY: scaleEyeRegionRadius(region?.radiusTopY ?? region?.radiusY ?? defaultEyePatchRadiusTopY, eyeRegionScale),
      radiusY: scaleEyeRegionRadius(region?.radiusY ?? defaultEyePatchRadiusY, eyeRegionScale),
      // Positive tilt lifts the outer corners, like the outer corner lift.
      rotation: round(-outwardSign * rotationMagnitude),
      scaleX,
      scaleY,
      stretchX,
      shiftX,
      shiftY,
      irisScale,
      tailLift,
      upperLid,
      lowerLid,
    };
  };

  return [createTransform("left"), createTransform("right")];
}

/**
 * Displacement of a point at (localX, localY) from the eye center, in the pixel unit of the radii.
 * Positive Y is down.
 */
export function getEyeDisplacement(
  transform: EyeMeshTransform,
  localX: number,
  localY: number,
  radiusX: number,
  radiusTopY: number,
  radiusBottomY: number,
) {
  const minRadiusY = Math.min(radiusTopY, radiusBottomY);
  const u = localX / radiusX;
  const v = localY / (localY < 0 ? radiusTopY : radiusBottomY);
  const distance = Math.hypot(u, v);
  let offsetX = 0;
  let offsetY = 0;

  // Size and height: bulge the eye together with its lash line.
  const bulgeWeight = flatFalloff(distance / eyeScaleSupport);
  offsetX += localX * (transform.scaleX - 1) * bulgeWeight;
  offsetY += localY * (transform.scaleY - 1) * bulgeWeight;

  // Length stretches only the inside of the selection; its edge stays pinned, so the region does not widen.
  offsetX += localX * transform.stretchX * peakedFalloff(distance);

  // Up/down and distance deform the eye in place instead of moving a rigid patch.
  const outerU = transform.outwardSign * u;
  const shiftWeight =
    flatFalloff(outerU > 0 ? outerU / eyeShiftOuterSupport : -outerU / eyeShiftInnerSupport) *
    flatFalloff(Math.abs(v) / (localY < 0 ? eyeShiftTopSupport : eyeShiftBottomSupport));
  offsetX += transform.outwardSign * transform.shiftX * radiusX * shiftWeight;
  offsetY += transform.shiftY * minRadiusY * shiftWeight;

  if (transform.rotation !== 0) {
    const rotationWeight = flatFalloff(distance / eyeRotationSupport);
    const cos = Math.cos(transform.rotation);
    const sin = Math.sin(transform.rotation);
    offsetX += (localX * cos - localY * sin - localX) * rotationWeight;
    offsetY += (localX * sin + localY * cos - localY) * rotationWeight;
  }

  if (transform.irisScale !== 0) {
    const irisRadius = eyeIrisSupport * Math.min(radiusX, (radiusTopY + radiusBottomY) / 2);
    const irisWeight = peakedFalloff(Math.hypot(localX, localY) / irisRadius);
    offsetX += localX * transform.irisScale * irisWeight;
    offsetY += localY * transform.irisScale * irisWeight;
  }

  if (transform.tailLift !== 0) {
    const tailWeight = peakedFalloff(Math.hypot((outerU - eyeTailCornerX) / eyeTailSupportX, v / eyeTailSupportY));
    offsetY -= transform.tailLift * minRadiusY * tailWeight;
  }

  if (transform.upperLid !== 0 || transform.lowerLid !== 0) {
    const lidWidthWeight = flatFalloff(Math.abs(u) / eyeLidSupportX);
    if (localY < 0) {
      offsetY -= transform.upperLid * radiusTopY * lidWidthWeight * peakedRamp(-v / eyeLidSupportY);
    } else {
      offsetY += transform.lowerLid * radiusBottomY * lidWidthWeight * peakedRamp(v / eyeLidSupportY);
    }
  }

  return { x: offsetX, y: offsetY };
}

export function createMouthMeshTransforms(recipe: EditRecipe, landmarks: ManualLandmarks): MouthMeshTransform[] {
  const { mouth } = recipe;
  const mouthHorizontal = mouth.mouthHorizontal / maxMouthHorizontalControlValue;
  const mouthVertical = mouth.mouthVertical / maxMouthVerticalControlValue;
  const mouthSmile = mouth.mouthSmile / maxMouthSmileControlValue;
  const mouthWidth = mouth.mouthWidth;
  const mouthSize = mouth.mouthSize;
  const scaleX = round(clamp(1 + mouthSize * 0.42 + mouthWidth * 0.62, 0.55, 1.5));
  const scaleY = round(clamp(1 + mouthSize * 0.38, 0.62, 1.42));
  const translateX = round(clamp(mouthHorizontal, -1, 1) * mouthTranslateXAmplitude);
  const translateY = round(clamp(mouthVertical, -1, 1) * mouthTranslateYAmplitude);
  const smile = round(clamp(mouthSmile, -1, 1));
  const hasTransform =
    Math.abs(scaleX - 1) >= 0.001 ||
    Math.abs(scaleY - 1) >= 0.001 ||
    Math.abs(translateX) >= 0.001 ||
    Math.abs(translateY) >= 0.001 ||
    Math.abs(smile) >= 0.001;

  if (!hasTransform) return [];

  const mouthWidthFromLandmarks = Math.abs(landmarks.mouthRight.x - landmarks.mouthLeft.x);
  const eyeDistance = Math.abs(landmarks.rightEye.x - landmarks.leftEye.x);
  const faceHeight = Math.abs(landmarks.chin.y - (landmarks.leftEye.y + landmarks.rightEye.y) / 2);
  // The patch must stay inside the face: scaling or moving it would otherwise drag the jaw line and chin, changing the face shape.
  const jawReach = Math.min(landmarks.mouthCenter.x - landmarks.jawLeft.x, landmarks.jawRight.x - landmarks.mouthCenter.x);
  const chinReach = landmarks.chin.y - landmarks.mouthCenter.y;
  const maxRadiusX = jawReach > 0 ? jawReach * mouthPatchFaceShare : Number.POSITIVE_INFINITY;
  const maxRadiusY = chinReach > 0 ? chinReach * mouthPatchFaceShare : Number.POSITIVE_INFINITY;
  const radiusX = round(Math.min(clamp(Math.max(mouthWidthFromLandmarks * 2.15, eyeDistance * 0.2, defaultMouthPatchRadiusX), 0.035, 0.2), maxRadiusX));
  const radiusY = round(Math.min(clamp(Math.max(faceHeight * 0.14, defaultMouthPatchRadiusY), 0.025, 0.12), maxRadiusY));

  return [
    {
      centerX: landmarks.mouthCenter.x,
      centerY: landmarks.mouthCenter.y,
      radiusX,
      radiusY,
      scaleX,
      scaleY,
      smile,
      translateX,
      translateY,
    },
  ];
}

export async function mountPixiStage(host: HTMLDivElement): Promise<PixiStageHandle> {
  if (typeof document === "undefined") {
    return createFallbackStage(host);
  }

  try {
    const pixi = await import("pixi.js");

    return await createPixiStage(host, pixi);
  } catch {
    return createFallbackStage(host);
  }
}

function createFallbackStage(host: HTMLDivElement): PixiStageHandle {
  const image = document.createElement("img");
  let isDestroyed = false;

  image.alt = "";
  image.style.height = "100%";
  image.style.objectFit = "contain";
  image.style.width = "100%";
  image.style.transition = "filter 120ms ease, transform 120ms ease";
  host.replaceChildren(image);

  return {
    async setImageUrl(url: string) {
      if (isDestroyed) return;
      image.src = url;
      // The DOM fallback cannot apply the deformation mesh. Keep save disabled.
      throw new Error("Image editing requires a working graphics renderer");
    },
    applyRecipe() {},
    async exportImage() {
      throw new Error("Image editing requires a working graphics renderer");
    },
    destroy() {
      if (isDestroyed) return;
      isDestroyed = true;
      image.remove();
    },
  };
}

async function createPixiStage(host: HTMLDivElement, pixi: PixiModule): Promise<PixiStageHandle> {
  const { Application, Container, MeshPlane } = pixi;
  const app = new Application();
  const imageContainer = new Container();
  let imageMesh: InstanceType<typeof MeshPlane> | null = null;
  let baseMeshPositions: Float32Array | null = null;
  let currentRecipe: EditRecipe | null = null;
  let imageLoadToken = 0;
  let imageReady: Promise<void> | null = null;
  let isDestroyed = false;

  const options: Partial<import("pixi.js").ApplicationOptions> & import("pixi.js").AccessibilitySystemOptions = {
    antialias: true,
    // This layer renders pixels only; keyboard controls and preview labels live in React DOM.
    // Pixi 8.10 rebinds global Tab listeners on activation, leaving stale handlers after destroy.
    accessibilityOptions: { activateOnTab: false, deactivateOnMouseMove: false },
    autoDensity: true,
    backgroundAlpha: 0,
    preserveDrawingBuffer: true,
    resizeTo: host,
    resolution: window.devicePixelRatio || 1,
  };
  await app.init(options);

  app.canvas.style.display = "block";
  app.canvas.style.height = "100%";
  app.canvas.style.width = "100%";
  host.replaceChildren(app.canvas);
  app.stage.addChild(imageContainer);

  const resizeObserver = new ResizeObserver(() => {
    if (isDestroyed) return;
    resizePixiAppToHost(app, host);
    fitImageToStage(app, imageMesh);
    if (currentRecipe) {
      applyPreview(app, imageContainer, imageMesh, baseMeshPositions, currentRecipe);
    }
    app.render();
  });
  resizeObserver.observe(host);

  return {
    setImageUrl(url: string) {
      imageReady = (async () => {
      const loadToken = imageLoadToken + 1;
      imageLoadToken = loadToken;
      const texture = await loadPixiTexture(pixi, url);
      if (isDestroyed || loadToken !== imageLoadToken) return;

      imageContainer.removeChildren();
      imageMesh = new MeshPlane({
        texture,
        verticesX: meshVerticesX,
        verticesY: meshVerticesY,
      });
      imageMesh.autoResize = false;
      baseMeshPositions = imageMesh.geometry.positions.slice();
      imageContainer.addChild(imageMesh);
      resizePixiAppToHost(app, host);
      fitImageToStage(app, imageMesh);

      if (currentRecipe) {
      applyPreview(app, imageContainer, imageMesh, baseMeshPositions, currentRecipe);
      }
      app.render();
      })();
      return imageReady;
    },
    applyRecipe(recipe: EditRecipe) {
      if (isDestroyed) return;
      currentRecipe = recipe;
      applyPreview(app, imageContainer, imageMesh, baseMeshPositions, recipe);
      app.render();
    },
    async exportImage() {
      while (imageReady) {
        const pending = imageReady;
        await pending;
        if (pending === imageReady) break;
      }
      if (!imageMesh) throw new Error("Pixi image is not ready");
      if (isDestroyed) {
        throw new Error("Pixi stage is already destroyed");
      }
      // Copy the already-deformed vertices, so export uses precisely the visible edit.
      // A separate mesh keeps viewport positioning, DPR, overlays and letterboxing out of PNGs.
      const exportMesh = new MeshPlane({
        texture: imageMesh.texture,
        verticesX: meshVerticesX,
        verticesY: meshVerticesY,
      });
      exportMesh.autoResize = false;
      exportMesh.geometry.positions = imageMesh.geometry.positions.slice();
      try {
        const canvas = app.renderer.extract.canvas({
          target: exportMesh,
          frame: new pixi.Rectangle(0, 0, imageMesh.texture.width, imageMesh.texture.height),
          resolution: 1,
          antialias: true,
          clearColor: [0, 0, 0, 0],
        });
        return await canvasToBlob(canvas);
      } finally {
        // The source texture is shared with the preview and must remain alive.
        exportMesh.destroy({ texture: false, textureSource: false });
      }
    },
    destroy() {
      if (isDestroyed) return;
      isDestroyed = true;
      imageLoadToken += 1;
      resizeObserver.disconnect();
      app.destroy(true, { children: true, texture: true, textureSource: true });
    },
  };
}

async function loadPixiTexture(pixi: PixiModule, url: string) {
  // Private authenticated asset URLs have no filename extension. Assets.load returns null for them.
  if (url.startsWith("blob:") || !/\.(?:png|jpe?g|webp|avif)(?:[?#]|$)/i.test(url)) {
    return loadBrowserImageTexture(pixi.Texture, url);
  }

  try {
    const texture = await pixi.Assets.load(url);
    if (texture && texture.width > 0 && texture.height > 0) return texture;
    return loadBrowserImageTexture(pixi.Texture, url);
  } catch {
    return loadBrowserImageTexture(pixi.Texture, url);
  }
}

function loadBrowserImageTexture(Texture: PixiModule["Texture"], url: string) {
  return new Promise<ReturnType<PixiModule["Texture"]["from"]>>((resolve, reject) => {
    const image = new Image();

    if (!url.startsWith("blob:") && !url.startsWith("data:")) {
      image.crossOrigin = "anonymous";
    }

    image.onload = () => {
      resolve(Texture.from(image));
    };
    image.onerror = () => {
      reject(new Error(`Unable to load editor image texture: ${url}`));
    };
    image.src = url;
  });
}

function fitImageToStage(
  app: { renderer: { height: number; width: number; screen?: { width: number; height: number } } },
  imageMesh: {
    scale: { set(x: number, y: number): void };
    texture: { height: number; width: number };
    x: number;
    y: number;
  } | null,
) {
  if (!imageMesh || !imageMesh.texture.width || !imageMesh.texture.height) return;

  const rendererWidth = Math.max(1, app.renderer.screen?.width ?? app.renderer.width);
  const rendererHeight = Math.max(1, app.renderer.screen?.height ?? app.renderer.height);
  const fitScale = Math.min(rendererWidth / imageMesh.texture.width, rendererHeight / imageMesh.texture.height);

  imageMesh.scale.set(fitScale, fitScale);
  imageMesh.x = (rendererWidth - imageMesh.texture.width * fitScale) / 2;
  imageMesh.y = (rendererHeight - imageMesh.texture.height * fitScale) / 2;
}

function applyPreview(
  app: { canvas?: HTMLCanvasElement; renderer: { height: number; width: number; screen?: { width: number; height: number } } },
  imageContainer: {
    position: { set(x: number, y: number): void };
    scale: { set(x: number, y: number): void };
    skew: { set(x: number, y: number): void };
  },
  imageMesh: {
    geometry: { positions: Float32Array };
    scale: { x: number; y: number };
    texture: { height: number; width: number };
  } | null,
  baseMeshPositions: Float32Array | null,
  recipe: EditRecipe,
): RecipePreview {
  const preview = calculateRecipePreview(recipe);
  const coordinates = recipe.imageCoordinates;
  if (coordinates && (!validImageCoordinates(coordinates) || (imageMesh &&
      (coordinates.width !== imageMesh.texture.width || coordinates.height !== imageMesh.texture.height)))) {
    throw new Error("Recipe image coordinates do not match the source");
  }

  imageContainer.position.set(
    preview.imageOffset.x + preview.eyeOffset.x,
    preview.imageOffset.y + preview.eyeOffset.y,
  );
  imageContainer.scale.set(
    preview.imageScale.x * preview.eyeScale.x,
    preview.imageScale.y * preview.eyeScale.y,
  );
  imageContainer.skew.set(preview.imageSkew.x + preview.eyeSkew, preview.imageSkew.y);

  const toImagePixels = (strokes: readonly LiquifyStroke[]) => coordinates
    ? strokes.map(stroke => ({ ...stroke, radius: stroke.radius * coordinates.effectScale }))
    : strokes;

  applyMeshDeformation(
    app,
    imageMesh,
    baseMeshPositions,
    {
      browTransforms: preview.browMeshTransforms,
      eyeTransforms: preview.eyeMeshTransforms,
      faceShape: preview.faceShapeTransform,
      mouthTransforms: preview.mouthMeshTransforms,
      protectionZones: preview.featureProtectionZones,
      strokes: [...toImagePixels(preview.featureLiquifyStrokes), ...recipe.liquify],
    },
    coordinates?.effectScale,
  );

  if (app.renderer.width === 0 || app.renderer.height === 0) {
    return preview;
  }

  return preview;
}

function getRendererPixelRatio(app: { canvas?: HTMLCanvasElement; renderer: { height: number; width: number; screen?: { width: number; height: number } } }) {
  if (app.renderer.screen) return 1;
  const rect = app.canvas?.getBoundingClientRect();
  if (rect?.width && rect.height) {
    return Math.max(app.renderer.width / rect.width, app.renderer.height / rect.height);
  }

  return typeof window === "undefined" ? 1 : window.devicePixelRatio || 1;
}

function getStrokeVector(
  stroke: LiquifyStroke,
  deltaX: number,
  deltaY: number,
  distance: number,
  radius: number,
) {
  if (stroke.mode === "warp") {
    return getWarpStrokeVector(stroke);
  }

  if (stroke.mode === "scale") {
    if (distance === 0) return { x: 0, y: 0 };
    const direction = clamp(stroke.scale ?? 0, -1, 1) >= 0 ? 1 : -1;

    return {
      x: (deltaX / radius) * direction,
      y: (deltaY / radius) * direction,
    };
  }

  if (stroke.mode === "expand" || stroke.mode === "shrink") {
    if (distance === 0) return { x: 0, y: 0 };
    const direction = stroke.mode === "expand" ? 1 : -1;

    return {
      x: (deltaX / radius) * direction,
      y: (deltaY / radius) * direction,
    };
  }

  return modeVector(stroke.mode);
}

function getWarpStrokeVector(stroke: LiquifyStroke) {
  const deltaX = clamp(stroke.deltaX ?? 0, -0.35, 0.35);
  const deltaY = clamp(stroke.deltaY ?? 0, -0.35, 0.35);
  const distance = Math.hypot(deltaX, deltaY);
  if (distance === 0) return { x: 0, y: 0 };

  const visibleDistance = clamp(distance / 0.12, 0, 1);

  return {
    x: deltaX / distance * visibleDistance,
    y: deltaY / distance * visibleDistance,
  };
}

/**
 * Mouth displacement for a point at (localX, localY) from the mouth center, in the pixel unit of the radii.
 * Every field fades smoothly to the patch edge, so the mouth deforms in place instead of moving as a patch.
 */
export function getMouthDisplacement(
  transform: MouthMeshTransform,
  localX: number,
  localY: number,
  radiusX: number,
  radiusY: number,
) {
  const u = localX / radiusX;
  const weight = flatFalloff(Math.hypot(u, localY / radiusY));
  if (weight === 0) return { x: 0, y: 0 };

  const horizontal = Math.min(1, Math.abs(u));
  const cornerWeight = horizontal ** 1.45;
  const centerWeight = (1 - horizontal) ** 2;
  const smileOffset = (-cornerWeight + centerWeight * 0.28) * transform.smile * mouthSmileAmplitude * radiusY;

  return {
    x: (localX * (transform.scaleX - 1) + transform.translateX * radiusX) * weight,
    y: (localY * (transform.scaleY - 1) + transform.translateY * radiusY + smileOffset) * weight,
  };
}

export type MeshDeformationInput = {
  textureWidth: number;
  textureHeight: number;
  /** Display pixels per texture pixel. */
  fitScale: number;
  /** Pixels per stroke-radius unit: the device pixel ratio, or the image effect scale of anchored recipes. */
  pixelRatio: number;
  /** Anchored recipes store stroke radii in image pixels already. */
  strokeRadiiInImagePixels: boolean;
  /** Face-shape edit; it never moves protected features. */
  faceShape: FaceShapeTransform | null;
  /** Proportion and manual liquify strokes. */
  strokes: readonly LiquifyStroke[];
  protectionZones: readonly FeatureProtectionZone[];
  eyeTransforms: readonly EyeMeshTransform[];
  browTransforms: readonly BrowMeshTransform[];
  mouthTransforms: readonly MouthMeshTransform[];
};

/** Maps a display-pixel point to its display-pixel offset, or returns null when nothing deforms. */
export function createMeshDeformer(input: MeshDeformationInput) {
  const { textureWidth, textureHeight, fitScale, pixelRatio } = input;
  if (!input.faceShape && input.strokes.length === 0 && input.eyeTransforms.length === 0 &&
      input.browTransforms.length === 0 && input.mouthTransforms.length === 0) {
    return null;
  }

  const displayWidth = textureWidth * fitScale;
  const displayHeight = textureHeight * fitScale;
  const maxDisplacement = maxMeshDisplacementCssPixels * pixelRatio;
  const maxAccumulatedDisplacement = maxAccumulatedMeshDisplacementCssPixels * pixelRatio;
  const eyes = input.eyeTransforms.map((transform) => ({
    transform,
    centerX: clamp(transform.centerX, 0, 1) * displayWidth,
    centerY: clamp(transform.centerY, 0, 1) * displayHeight,
    radiusX: Math.max(12 * pixelRatio, transform.radiusX * displayWidth),
    radiusTopY: Math.max(10 * pixelRatio, transform.radiusTopY * displayHeight),
    radiusBottomY: Math.max(10 * pixelRatio, transform.radiusBottomY * displayHeight),
  }));
  const brows = input.browTransforms.map((transform) => {
    const geometry = {
      innerX: transform.inner.x * displayWidth,
      innerY: transform.inner.y * displayHeight,
      outerX: transform.outer.x * displayWidth,
      outerY: transform.outer.y * displayHeight,
      peakX: transform.peak.x * displayWidth,
      peakY: transform.peak.y * displayHeight,
      minHalfLength: 6 * pixelRatio,
    };
    const halfLength = Math.max(
      geometry.minHalfLength,
      Math.hypot(geometry.outerX - geometry.innerX, geometry.outerY - geometry.innerY) / 2,
    );
    return {
      transform,
      geometry,
      centerX: (geometry.innerX + geometry.outerX) / 2,
      centerY: (geometry.innerY + geometry.outerY) / 2,
      reach: halfLength * browMaxSupport,
    };
  });
  const faceShape = input.faceShape ? createFaceShapeDeformer(input.faceShape, displayWidth, displayHeight) : null;
  const mouths = input.mouthTransforms.map((transform) => ({
    transform,
    centerX: clamp(transform.centerX, 0, 1) * displayWidth,
    centerY: clamp(transform.centerY, 0, 1) * displayHeight,
    radiusX: Math.max(10 * pixelRatio, transform.radiusX * displayWidth),
    radiusY: Math.max(8 * pixelRatio, transform.radiusY * displayHeight),
  }));

  const strokeOffset = (strokes: readonly LiquifyStroke[], displayX: number, displayY: number) => {
    let offsetX = 0;
    let offsetY = 0;
    for (const stroke of strokes) {
      const centerX = clamp(stroke.x, 0, 1) * displayWidth;
      const centerY = clamp(stroke.y, 0, 1) * displayHeight;
      const radius = Math.max(4 * pixelRatio, stroke.radius * (input.strokeRadiiInImagePixels ? 1 : pixelRatio));
      const deltaX = displayX - centerX;
      const deltaY = displayY - centerY;
      const distance = Math.hypot(deltaX, deltaY);
      if (distance > radius) continue;

      const falloff = (1 - distance / radius) ** 2 * clamp(stroke.strength, 0, 1);

      if (stroke.mode === "warp") {
        offsetX += clamp(stroke.deltaX ?? 0, -0.35, 0.35) * displayWidth * falloff;
        offsetY += clamp(stroke.deltaY ?? 0, -0.35, 0.35) * displayHeight * falloff;
        continue;
      }

      if (stroke.mode === "scale") {
        if (distance === 0) continue;
        const scale = clamp(stroke.scale ?? 0, -1, 1);
        const scaleOffset = radius * scale * (distance / radius) * falloff * 1.4;
        offsetX += (deltaX / distance) * scaleOffset;
        offsetY += (deltaY / distance) * scaleOffset;
        continue;
      }

      const vector = getStrokeVector(stroke, deltaX, deltaY, distance, radius);
      offsetX += vector.x * falloff * maxDisplacement;
      offsetY += vector.y * falloff * maxDisplacement;
    }
    return { x: offsetX, y: offsetY };
  };

  return (displayX: number, displayY: number) => {
    let offsetX = 0;
    let offsetY = 0;

    if (faceShape) {
      const shape = faceShape(displayX, displayY);
      if (shape.x !== 0 || shape.y !== 0) {
        const free = 1 - getFeatureProtection(input.protectionZones, displayX / displayWidth, displayY / displayHeight);
        offsetX += shape.x * free;
        offsetY += shape.y * free;
      }
    }

    const other = strokeOffset(input.strokes, displayX, displayY);
    offsetX += other.x;
    offsetY += other.y;

    // Feature edits follow features that proportion or liquify strokes have already moved.
    for (const eye of eyes) {
      const localX = displayX + offsetX - eye.centerX;
      const localY = displayY + offsetY - eye.centerY;
      if (Math.abs(localX) > eye.radiusX * eyeMaxSupportX ||
          localY < -eye.radiusTopY * eyeMaxSupportY || localY > eye.radiusBottomY * eyeMaxSupportY) continue;

      const displacement = getEyeDisplacement(eye.transform, localX, localY, eye.radiusX, eye.radiusTopY, eye.radiusBottomY);
      offsetX += displacement.x;
      offsetY += displacement.y;
    }

    for (const brow of brows) {
      const x = displayX + offsetX;
      const y = displayY + offsetY;
      if (Math.abs(x - brow.centerX) > brow.reach || Math.abs(y - brow.centerY) > brow.reach) continue;

      const displacement = getBrowDisplacement(brow.transform, brow.geometry, x, y);
      offsetX += displacement.x;
      offsetY += displacement.y;
    }

    for (const mouth of mouths) {
      const localX = displayX + offsetX - mouth.centerX;
      const localY = displayY + offsetY - mouth.centerY;
      if (Math.abs(localX) > mouth.radiusX || Math.abs(localY) > mouth.radiusY) continue;

      const displacement = getMouthDisplacement(mouth.transform, localX, localY, mouth.radiusX, mouth.radiusY);
      offsetX += displacement.x;
      offsetY += displacement.y;
    }

    const offsetLength = Math.hypot(offsetX, offsetY);
    if (offsetLength > maxAccumulatedDisplacement) {
      const clampScale = maxAccumulatedDisplacement / offsetLength;
      offsetX *= clampScale;
      offsetY *= clampScale;
    }

    return { x: offsetX, y: offsetY };
  };
}

function applyMeshDeformation(
  app: { canvas?: HTMLCanvasElement; renderer: { height: number; width: number; screen?: { width: number; height: number } } },
  imageMesh: {
    geometry: { positions: Float32Array };
    scale: { x: number; y: number };
    texture: { height: number; width: number };
  } | null,
  baseMeshPositions: Float32Array | null,
  deformation: Omit<MeshDeformationInput, "fitScale" | "pixelRatio" | "strokeRadiiInImagePixels" | "textureHeight" | "textureWidth">,
  imageEffectScale?: number,
) {
  if (!imageMesh || !baseMeshPositions) return;

  const fitScale = imageEffectScale === undefined ? imageMesh.scale.x || 1 : 1;
  const nextPositions = baseMeshPositions.slice();
  const deform = createMeshDeformer({
    ...deformation,
    fitScale,
    pixelRatio: imageEffectScale ?? getRendererPixelRatio(app),
    strokeRadiiInImagePixels: imageEffectScale !== undefined,
    textureHeight: Math.max(1, imageMesh.texture.height),
    textureWidth: Math.max(1, imageMesh.texture.width),
  });

  if (deform) {
    for (let index = 0; index < baseMeshPositions.length; index += 2) {
      const baseX = baseMeshPositions[index];
      const baseY = baseMeshPositions[index + 1];
      const offset = deform(baseX * fitScale, baseY * fitScale);
      nextPositions[index] = baseX + offset.x / fitScale;
      nextPositions[index + 1] = baseY + offset.y / fitScale;
    }
  }

  imageMesh.geometry.positions = nextPositions;
}
