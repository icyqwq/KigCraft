import { createDefaultLandmarks, type ManualLandmarks } from "./landmarks";
import { eyeControlRanges, mouthControlRanges, type EditRecipe, type LiquifyMode, type LiquifyStroke } from "./recipe";

type PixiModule = typeof import("pixi.js");

export type PixiStageHandle = {
  setImageUrl(url: string): Promise<void>;
  applyRecipe(recipe: EditRecipe): void;
  exportImage(): Promise<Blob>;
  destroy(): void;
};

export type EyeMeshTransform = {
  centerX: number;
  centerY: number;
  radiusBottomY: number;
  radiusX: number;
  radiusTopY: number;
  radiusY: number;
  rotation: number;
  scaleX: number;
  scaleY: number;
  translateX: number;
  translateY: number;
};

export type MouthMeshTransform = {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  scaleX: number;
  scaleY: number;
  smile: number;
  translateX: number;
  translateY: number;
};

export type RecipePreview = {
  allLiquifyStrokes: LiquifyStroke[];
  displacementScale: { x: number; y: number };
  detailRegionCount: number;
  eyeMeshTransforms: EyeMeshTransform[];
  eyeOffset: { x: number; y: number };
  eyeScale: { x: number; y: number };
  eyeSkew: number;
  eyeTransformCount: number;
  featureLiquifyStrokes: LiquifyStroke[];
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

function canvasToBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
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
const meshVerticesX = 65;
const meshVerticesY = 89;
const maxMeshDisplacementCssPixels = 84;
const maxAccumulatedMeshDisplacementCssPixels = 128;
const maxEyeDistanceControlValue = eyeControlRanges.eyeDistance.max;
const maxEyeVerticalControlValue = eyeControlRanges.eyeVertical.max;
const maxEyeDistanceTranslateCssPixels = 34;
const maxEyeVerticalTranslateCssPixels = 28;
const maxEyeTiltRadians = 0.18;
const eyeAffineCorePlateau = 0.78;
const defaultEyePatchRadiusX = 0.12;
const defaultEyePatchRadiusY = 0.085;
const defaultEyePatchRadiusTopY = 0.12;
const defaultEyePatchRadiusBottomY = 0.075;
const maxMouthHorizontalControlValue = mouthControlRanges.mouthHorizontal.max;
const maxMouthVerticalControlValue = mouthControlRanges.mouthVertical.max;
const maxMouthSmileControlValue = mouthControlRanges.mouthSmile.max;
const maxMouthTranslateXCssPixels = 26;
const maxMouthTranslateYCssPixels = 24;
const maxMouthSmileCssPixels = 18;
const mouthAffineCorePlateau = 0.62;
const defaultMouthPatchRadiusX = 0.08;
const defaultMouthPatchRadiusY = 0.045;

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
  const featureLiquifyStrokes = createFeatureLiquifyStrokes(recipe, landmarks);
  const eyeMeshTransforms = createEyeMeshTransforms(recipe, landmarks);
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
      const scaleX = Math.abs(transform.scaleX - 1);
      const scaleY = Math.abs(transform.scaleY - 1);
      const rotation = Math.abs(transform.rotation);
      const translateX = Math.abs(transform.translateX) / maxEyeDistanceTranslateCssPixels;
      const translateY = Math.abs(transform.translateY) / maxEyeVerticalTranslateCssPixels;

      return {
        x: Math.max(accumulator.x, translateX, scaleX * 1.2, rotation * 1.4),
        y: Math.max(accumulator.y, translateY, scaleY * 1.2, rotation * 1.4),
        intensity: Math.max(accumulator.intensity, translateX, translateY, scaleX, scaleY, rotation),
      };
    },
    { x: 0, y: 0, intensity: 0 },
  );
  const mouthTransformVector = mouthMeshTransforms.reduce(
    (accumulator, transform) => {
      const scaleX = Math.abs(transform.scaleX - 1);
      const scaleY = Math.abs(transform.scaleY - 1);
      const translateX = Math.abs(transform.translateX) / maxMouthTranslateXCssPixels;
      const translateY = Math.abs(transform.translateY) / maxMouthTranslateYCssPixels;
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
    detailRegionCount: 0,
    displacementScale: {
      x: scaleAxisWeight(Math.max(liquifyVector.x, eyeTransformVector.x, mouthTransformVector.x)),
      y: scaleAxisWeight(Math.max(liquifyVector.y, eyeTransformVector.y, mouthTransformVector.y)),
    },
    eyeMeshTransforms,
    eyeOffset: { x: 0, y: 0 },
    eyeScale: { x: 1, y: 1 },
    eyeSkew: 0,
    eyeTransformCount: eyeMeshTransforms.length,
    featureLiquifyStrokes,
    featureStrokeCount: featureLiquifyStrokes.length,
    imageOffset: { x: 0, y: 0 },
    imageScale: { x: 1, y: 1 },
    imageSkew: { x: 0, y: 0 },
    jawScale: { x: 1, y: 1 },
    liquifyIntensity: round(Math.max(liquifyVector.intensity, eyeTransformVector.intensity, mouthTransformVector.intensity)),
    manualStrokeCount: recipe.liquify.length,
    mouthMeshTransforms,
    mouthTransformCount: mouthMeshTransforms.length,
    strokeCount: allLiquifyStrokes.length,
  };
}

function makeStroke(x: number, y: number, mode: LiquifyMode, strength: number, radius: number): LiquifyStroke | null {
  const normalizedStrength = clamp(Math.abs(strength), 0, 1);
  if (normalizedStrength < 0.01) return null;

  return {
    mode,
    radius,
    strength: round(normalizedStrength),
    x: round(clamp(x, 0, 1)),
    y: round(clamp(y, 0, 1)),
  };
}

function pushStroke(
  strokes: LiquifyStroke[],
  x: number,
  y: number,
  mode: LiquifyMode,
  strength: number,
  radius: number,
) {
  const stroke = makeStroke(x, y, mode, strength, radius);
  if (stroke) {
    strokes.push(stroke);
  }
}

export function createFeatureLiquifyStrokes(recipe: EditRecipe, landmarks: ManualLandmarks): LiquifyStroke[] {
  const strokes: LiquifyStroke[] = [];
  const { face } = recipe;
  const cheekY = round((landmarks.jawLeft.y + landmarks.leftEye.y) / 2);
  const cheekLeftX = round((landmarks.jawLeft.x + landmarks.leftEye.x) / 2);
  const cheekRightX = round((landmarks.jawRight.x + landmarks.rightEye.x) / 2);

  const faceWidth = face.faceWidth;
  if (faceWidth !== 0) {
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.jawLeft.y, faceWidth < 0 ? "push-right" : "push-left", faceWidth * 0.62, 110);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.jawRight.y, faceWidth < 0 ? "push-left" : "push-right", faceWidth * 0.62, 110);
  }

  const faceLength = face.faceLength;
  if (faceLength !== 0) {
    const eyeY = round((landmarks.leftEye.y + landmarks.rightEye.y) / 2);
    const foreheadY = round(Math.max(0, eyeY - Math.abs(landmarks.chin.y - eyeY) * 0.7));
    pushStroke(strokes, landmarks.chin.x, foreheadY, faceLength > 0 ? "push-up" : "push-down", faceLength * 0.3, 128);
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.jawLeft.y, faceLength > 0 ? "push-down" : "push-up", faceLength * 0.34, 132);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.jawRight.y, faceLength > 0 ? "push-down" : "push-up", faceLength * 0.34, 132);
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.jawLeft.y, faceLength > 0 ? "push-right" : "push-left", faceLength * 0.14, 120);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.jawRight.y, faceLength > 0 ? "push-left" : "push-right", faceLength * 0.14, 120);
    pushStroke(strokes, landmarks.chin.x, landmarks.chin.y, faceLength > 0 ? "push-down" : "push-up", faceLength * 0.36, 140);
  }

  const midFaceLength = face.midFaceLength;
  if (midFaceLength !== 0) {
    const eyeY = round((landmarks.leftEye.y + landmarks.rightEye.y) / 2);
    const mouthY = landmarks.mouthCenter.y;
    const midFaceY = round((eyeY + mouthY) / 2);
    const lowerCheekY = round((cheekY + mouthY) / 2);
    const mode = midFaceLength > 0 ? "push-down" : "push-up";

    pushStroke(strokes, landmarks.mouthCenter.x, midFaceY, mode, midFaceLength * 0.32, 96);
    pushStroke(strokes, landmarks.mouthCenter.x, mouthY, mode, midFaceLength * 0.62, 112);
    pushStroke(strokes, cheekLeftX, lowerCheekY, mode, midFaceLength * 0.28, 104);
    pushStroke(strokes, cheekRightX, lowerCheekY, mode, midFaceLength * 0.28, 104);
  }

  const smallFace = face.smallFace;
  if (smallFace !== 0) {
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.jawLeft.y, "push-right", smallFace * 0.58, 120);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.jawRight.y, "push-left", smallFace * 0.58, 120);
    pushStroke(strokes, landmarks.chin.x, landmarks.chin.y, "push-up", smallFace * 0.44, 96);
  }

  const cheekbone = face.cheekbone;
  if (cheekbone !== 0) {
    pushStroke(strokes, cheekLeftX, cheekY, cheekbone < 0 ? "push-right" : "push-left", cheekbone * 0.52, 88);
    pushStroke(strokes, cheekRightX, cheekY, cheekbone < 0 ? "push-left" : "push-right", cheekbone * 0.52, 88);
  }

  const chinLength = face.chinLength;
  pushStroke(strokes, landmarks.chin.x, landmarks.chin.y, chinLength > 0 ? "push-down" : "push-up", chinLength * 0.62, 90);

  const chinPoint = face.chinPoint;
  if (chinPoint !== 0) {
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.chin.y, "push-right", chinPoint * 0.48, 78);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.chin.y, "push-left", chinPoint * 0.48, 78);
    pushStroke(strokes, landmarks.chin.x, landmarks.chin.y, chinPoint > 0 ? "push-down" : "push-up", chinPoint * 0.34, 72);
  }

  const vLine = face.vLine;
  if (vLine !== 0) {
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.jawLeft.y, "push-right", vLine * 0.7, 112);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.jawRight.y, "push-left", vLine * 0.7, 112);
    pushStroke(strokes, landmarks.chin.x, landmarks.chin.y, "push-down", vLine * 0.34, 78);
  }

  const jawAngle = face.jawAngle;
  if (jawAngle !== 0) {
    pushStroke(strokes, landmarks.jawLeft.x, landmarks.jawLeft.y, jawAngle > 0 ? "push-left" : "push-right", jawAngle * 0.48, 92);
    pushStroke(strokes, landmarks.jawRight.x, landmarks.jawRight.y, jawAngle > 0 ? "push-right" : "push-left", jawAngle * 0.48, 92);
  }

  return strokes;
}

export function createEyeMeshTransforms(recipe: EditRecipe, landmarks: ManualLandmarks): EyeMeshTransform[] {
  const { eyes } = recipe;
  const eyeSize = eyes.eyeSize;
  const eyeHeight = eyes.eyeHeight;
  const eyeWidth = eyes.eyeWidth;
  const eyeDistance = eyes.eyeDistance / maxEyeDistanceControlValue;
  const eyeVertical = eyes.eyeVertical / maxEyeVerticalControlValue;
  const eyeTilt = eyes.eyeTilt;
  const eyeRegionScale = getEyeRegionScaleMultiplier(eyes.eyeRegionScale);
  const scaleX = round(clamp(1 + eyeSize * 0.3 + eyeWidth * 0.22, 0.62, 1.48));
  const scaleY = round(clamp(1 + eyeSize * 0.3 + eyeHeight * 0.34, 0.58, 1.54));
  const translateY = round(eyeVertical * maxEyeVerticalTranslateCssPixels);
  const rotationMagnitude = round(eyeTilt * maxEyeTiltRadians);
  const hasTransform =
    Math.abs(scaleX - 1) >= 0.001 ||
    Math.abs(scaleY - 1) >= 0.001 ||
    Math.abs(eyeDistance) >= 0.001 ||
    Math.abs(translateY) >= 0.001 ||
    Math.abs(rotationMagnitude) >= 0.001;

  if (!hasTransform) return [];

  return [
    {
      centerX: landmarks.leftEye.x,
      centerY: landmarks.leftEye.y,
      radiusBottomY: scaleEyeRegionRadius(
        landmarks.eyeRegions?.left.radiusBottomY ?? landmarks.eyeRegions?.left.radiusY ?? defaultEyePatchRadiusBottomY,
        eyeRegionScale,
      ),
      radiusX: scaleEyeRegionRadius(landmarks.eyeRegions?.left.radiusX ?? defaultEyePatchRadiusX, eyeRegionScale),
      radiusTopY: scaleEyeRegionRadius(
        landmarks.eyeRegions?.left.radiusTopY ?? landmarks.eyeRegions?.left.radiusY ?? defaultEyePatchRadiusTopY,
        eyeRegionScale,
      ),
      radiusY: scaleEyeRegionRadius(landmarks.eyeRegions?.left.radiusY ?? defaultEyePatchRadiusY, eyeRegionScale),
      rotation: round(-rotationMagnitude),
      scaleX,
      scaleY,
      translateX: round(-eyeDistance * maxEyeDistanceTranslateCssPixels),
      translateY,
    },
    {
      centerX: landmarks.rightEye.x,
      centerY: landmarks.rightEye.y,
      radiusBottomY: scaleEyeRegionRadius(
        landmarks.eyeRegions?.right.radiusBottomY ?? landmarks.eyeRegions?.right.radiusY ?? defaultEyePatchRadiusBottomY,
        eyeRegionScale,
      ),
      radiusX: scaleEyeRegionRadius(landmarks.eyeRegions?.right.radiusX ?? defaultEyePatchRadiusX, eyeRegionScale),
      radiusTopY: scaleEyeRegionRadius(
        landmarks.eyeRegions?.right.radiusTopY ?? landmarks.eyeRegions?.right.radiusY ?? defaultEyePatchRadiusTopY,
        eyeRegionScale,
      ),
      radiusY: scaleEyeRegionRadius(landmarks.eyeRegions?.right.radiusY ?? defaultEyePatchRadiusY, eyeRegionScale),
      rotation: rotationMagnitude,
      scaleX,
      scaleY,
      translateX: round(eyeDistance * maxEyeDistanceTranslateCssPixels),
      translateY,
    },
  ];
}

export function createMouthMeshTransforms(recipe: EditRecipe, landmarks: ManualLandmarks): MouthMeshTransform[] {
  const { mouth } = recipe;
  const mouthHorizontal = mouth.mouthHorizontal / maxMouthHorizontalControlValue;
  const mouthVertical = mouth.mouthVertical / maxMouthVerticalControlValue;
  const mouthSmile = mouth.mouthSmile / maxMouthSmileControlValue;
  const mouthWidth = mouth.mouthWidth;
  const mouthSize = mouth.mouthSize;
  const scaleX = round(clamp(1 + mouthSize * 0.42 + mouthWidth * 0.74, 0.55, 1.72));
  const scaleY = round(clamp(1 + mouthSize * 0.38, 0.62, 1.42));
  const translateX = round(mouthHorizontal * maxMouthTranslateXCssPixels);
  const translateY = round(mouthVertical * maxMouthTranslateYCssPixels);
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
  const radiusX = round(clamp(Math.max(mouthWidthFromLandmarks * 2.15, eyeDistance * 0.2, defaultMouthPatchRadiusX), 0.035, 0.2));
  const radiusY = round(clamp(Math.max(faceHeight * 0.14, defaultMouthPatchRadiusY), 0.025, 0.12));

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
    },
    applyRecipe(recipe: EditRecipe) {
      if (isDestroyed) return;
      const preview = calculateRecipePreview(recipe);
      image.style.filter = `saturate(${1 + preview.liquifyIntensity * 0.04})`;
      image.style.transform = [
        `translate(${preview.imageOffset.x}px, ${preview.imageOffset.y}px)`,
        `scale(${preview.imageScale.x}, ${preview.imageScale.y})`,
        `skew(${preview.imageSkew.x}rad, ${preview.imageSkew.y}rad)`,
      ].join(" ");
    },
    async exportImage() {
      const canvas = document.createElement("canvas");
      canvas.width = Math.max(1, image.naturalWidth || host.clientWidth || 1);
      canvas.height = Math.max(1, image.naturalHeight || host.clientHeight || 1);
      const context = canvas.getContext("2d");
      if (!context) {
        throw new Error("Unable to create Pixi fallback export canvas");
      }
      context.drawImage(image, 0, 0, canvas.width, canvas.height);
      return canvasToBlob(canvas);
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
  let isDestroyed = false;

  await app.init({
    antialias: true,
    autoDensity: true,
    backgroundAlpha: 0,
    resizeTo: host,
    resolution: window.devicePixelRatio || 1,
  });

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
    async setImageUrl(url: string) {
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
    },
    applyRecipe(recipe: EditRecipe) {
      if (isDestroyed) return;
      currentRecipe = recipe;
      applyPreview(app, imageContainer, imageMesh, baseMeshPositions, recipe);
      app.render();
    },
    async exportImage() {
      if (isDestroyed) {
        throw new Error("Pixi stage is already destroyed");
      }
      app.render();
      return canvasToBlob(app.canvas);
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
  if (url.startsWith("blob:")) {
    return loadBrowserImageTexture(pixi.Texture, url);
  }

  try {
    return await pixi.Assets.load(url);
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
  app: { renderer: { height: number; width: number } },
  imageMesh: {
    scale: { set(x: number, y: number): void };
    texture: { height: number; width: number };
    x: number;
    y: number;
  } | null,
) {
  if (!imageMesh || !imageMesh.texture.width || !imageMesh.texture.height) return;

  const rendererWidth = Math.max(1, app.renderer.width);
  const rendererHeight = Math.max(1, app.renderer.height);
  const fitScale = Math.min(rendererWidth / imageMesh.texture.width, rendererHeight / imageMesh.texture.height);

  imageMesh.scale.set(fitScale, fitScale);
  imageMesh.x = (rendererWidth - imageMesh.texture.width * fitScale) / 2;
  imageMesh.y = (rendererHeight - imageMesh.texture.height * fitScale) / 2;
}

function applyPreview(
  app: { canvas?: HTMLCanvasElement; renderer: { height: number; width: number } },
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

  imageContainer.position.set(
    preview.imageOffset.x + preview.eyeOffset.x,
    preview.imageOffset.y + preview.eyeOffset.y,
  );
  imageContainer.scale.set(
    preview.imageScale.x * preview.eyeScale.x,
    preview.imageScale.y * preview.eyeScale.y,
  );
  imageContainer.skew.set(preview.imageSkew.x + preview.eyeSkew, preview.imageSkew.y);

  applyMeshDeformation(
    app,
    imageMesh,
    baseMeshPositions,
    preview.allLiquifyStrokes,
    preview.eyeMeshTransforms,
    preview.mouthMeshTransforms,
  );

  if (app.renderer.width === 0 || app.renderer.height === 0) {
    return preview;
  }

  return preview;
}

function getRendererPixelRatio(app: { canvas?: HTMLCanvasElement; renderer: { height: number; width: number } }) {
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

function getEyePatchFalloff(normalizedDistance: number) {
  if (normalizedDistance <= eyeAffineCorePlateau) return 1;

  const edgeBlend = clamp((1 - normalizedDistance) / (1 - eyeAffineCorePlateau), 0, 1);
  return edgeBlend * edgeBlend * (3 - 2 * edgeBlend);
}

function getMouthPatchFalloff(normalizedDistance: number) {
  if (normalizedDistance <= mouthAffineCorePlateau) return 1;

  const edgeBlend = clamp((1 - normalizedDistance) / (1 - mouthAffineCorePlateau), 0, 1);
  return edgeBlend * edgeBlend * (3 - 2 * edgeBlend);
}

function applyMeshDeformation(
  app: { canvas?: HTMLCanvasElement; renderer: { height: number; width: number } },
  imageMesh: {
    geometry: { positions: Float32Array };
    scale: { x: number; y: number };
    texture: { height: number; width: number };
  } | null,
  baseMeshPositions: Float32Array | null,
  strokes: readonly LiquifyStroke[],
  eyeTransforms: readonly EyeMeshTransform[],
  mouthTransforms: readonly MouthMeshTransform[],
) {
  if (!imageMesh || !baseMeshPositions) return;

  const textureWidth = Math.max(1, imageMesh.texture.width);
  const textureHeight = Math.max(1, imageMesh.texture.height);
  const fitScale = imageMesh.scale.x || 1;
  const rendererPixelRatio = getRendererPixelRatio(app);
  const maxDisplacement = maxMeshDisplacementCssPixels * rendererPixelRatio;
  const maxAccumulatedDisplacement = maxAccumulatedMeshDisplacementCssPixels * rendererPixelRatio;
  const nextPositions = baseMeshPositions.slice();

  if (strokes.length === 0 && eyeTransforms.length === 0 && mouthTransforms.length === 0) {
    imageMesh.geometry.positions = nextPositions;
    return;
  }

  for (let index = 0; index < baseMeshPositions.length; index += 2) {
    const baseX = baseMeshPositions[index];
    const baseY = baseMeshPositions[index + 1];
    const displayX = baseX * fitScale;
    const displayY = baseY * fitScale;
    let offsetX = 0;
    let offsetY = 0;

    for (const stroke of strokes) {
      const centerX = clamp(stroke.x, 0, 1) * textureWidth * fitScale;
      const centerY = clamp(stroke.y, 0, 1) * textureHeight * fitScale;
      const radius = Math.max(4 * rendererPixelRatio, stroke.radius * rendererPixelRatio);
      const deltaX = displayX - centerX;
      const deltaY = displayY - centerY;
      const distance = Math.hypot(deltaX, deltaY);
      if (distance > radius) continue;

      const falloff = (1 - distance / radius) ** 2 * clamp(stroke.strength, 0, 1);

      if (stroke.mode === "warp") {
        offsetX += clamp(stroke.deltaX ?? 0, -0.35, 0.35) * textureWidth * fitScale * falloff;
        offsetY += clamp(stroke.deltaY ?? 0, -0.35, 0.35) * textureHeight * fitScale * falloff;
        continue;
      }

      if (stroke.mode === "scale") {
        if (distance === 0) continue;
        const scale = clamp(stroke.scale ?? 0, -1, 1);
        const normalizedDistance = distance / radius;
        const scaleOffset = radius * scale * normalizedDistance * falloff * 1.4;
        offsetX += (deltaX / distance) * scaleOffset;
        offsetY += (deltaY / distance) * scaleOffset;
        continue;
      }

      const vector = getStrokeVector(stroke, deltaX, deltaY, distance, radius);
      offsetX += vector.x * falloff * maxDisplacement;
      offsetY += vector.y * falloff * maxDisplacement;
    }

    for (const transform of eyeTransforms) {
      const centerX = clamp(transform.centerX, 0, 1) * textureWidth * fitScale;
      const centerY = clamp(transform.centerY, 0, 1) * textureHeight * fitScale;
      const radiusX = Math.max(12 * rendererPixelRatio, transform.radiusX * textureWidth * fitScale);
      const currentDisplayX = displayX + offsetX;
      const currentDisplayY = displayY + offsetY;
      const localX = currentDisplayX - centerX;
      const localY = currentDisplayY - centerY;
      const radiusY =
        localY < 0
          ? Math.max(10 * rendererPixelRatio, transform.radiusTopY * textureHeight * fitScale)
          : Math.max(10 * rendererPixelRatio, transform.radiusBottomY * textureHeight * fitScale);
      const normalizedDistance = Math.hypot(localX / radiusX, localY / radiusY);
      if (normalizedDistance > 1) continue;

      const patchFalloff = getEyePatchFalloff(normalizedDistance);
      const scaledX = localX * transform.scaleX;
      const scaledY = localY * transform.scaleY;
      const cos = Math.cos(transform.rotation);
      const sin = Math.sin(transform.rotation);
      const rotatedX = scaledX * cos - scaledY * sin;
      const rotatedY = scaledX * sin + scaledY * cos;

      offsetX += ((rotatedX - localX) + transform.translateX * rendererPixelRatio) * patchFalloff;
      offsetY += ((rotatedY - localY) + transform.translateY * rendererPixelRatio) * patchFalloff;
    }

    for (const transform of mouthTransforms) {
      const centerX = clamp(transform.centerX, 0, 1) * textureWidth * fitScale;
      const centerY = clamp(transform.centerY, 0, 1) * textureHeight * fitScale;
      const radiusX = Math.max(10 * rendererPixelRatio, transform.radiusX * textureWidth * fitScale);
      const radiusY = Math.max(8 * rendererPixelRatio, transform.radiusY * textureHeight * fitScale);
      const currentDisplayX = displayX + offsetX;
      const currentDisplayY = displayY + offsetY;
      const localX = currentDisplayX - centerX;
      const localY = currentDisplayY - centerY;
      const normalizedDistance = Math.hypot(localX / radiusX, localY / radiusY);
      if (normalizedDistance > 1) continue;

      const patchFalloff = getMouthPatchFalloff(normalizedDistance);
      const scaledX = localX * transform.scaleX;
      const scaledY = localY * transform.scaleY;
      const horizontalPosition = clamp(localX / radiusX, -1, 1);
      const cornerWeight = Math.abs(horizontalPosition) ** 1.45;
      const centerWeight = (1 - Math.abs(horizontalPosition)) ** 2;
      const smileOffset =
        (-transform.smile * cornerWeight + transform.smile * centerWeight * 0.28) *
        maxMouthSmileCssPixels *
        rendererPixelRatio;

      offsetX += ((scaledX - localX) + transform.translateX * rendererPixelRatio) * patchFalloff;
      offsetY += ((scaledY - localY) + transform.translateY * rendererPixelRatio + smileOffset) * patchFalloff;
    }

    const offsetLength = Math.hypot(offsetX, offsetY);
    if (offsetLength > maxAccumulatedDisplacement) {
      const clampScale = maxAccumulatedDisplacement / offsetLength;
      offsetX *= clampScale;
      offsetY *= clampScale;
    }

    nextPositions[index] = baseX + offsetX / fitScale;
    nextPositions[index + 1] = baseY + offsetY / fitScale;
  }

  imageMesh.geometry.positions = nextPositions;
}

