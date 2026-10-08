import { coreFalloff, flatFalloff } from "./falloff";
import { resolveBrowLandmarks, type LandmarkPoint, type ManualLandmarks } from "./landmarks";
import type { EditRecipe, FaceControlKey, LiquifyMode, LiquifyStroke } from "./recipe";

/** Ellipse in normalized image coordinates whose content face-shape edits must not move. */
export type FeatureProtectionZone = {
  centerX: number;
  centerY: number;
  radiusX: number;
  radiusY: number;
  /** Protection is full inside the zone and fades out by this multiple of its radius. */
  fadeOut: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function round(value: number) {
  return Number(value.toFixed(4));
}

function pushStroke(strokes: LiquifyStroke[], point: LandmarkPoint, mode: LiquifyMode, strength: number, radius: number) {
  const normalizedStrength = clamp(Math.abs(strength), 0, 1);
  if (normalizedStrength < 0.01) return;

  strokes.push({
    mode,
    radius,
    strength: round(normalizedStrength),
    x: round(clamp(point.x, 0, 1)),
    y: round(clamp(point.y, 0, 1)),
  });
}

/** Face-shape controls; the remaining face controls are proportions. */
export type FaceShapeControlKey = Exclude<FaceControlKey, "faceLength" | "midFaceLength">;

type ContourProfile = {
  /** Toward the face center line, in percent of face width at the default slider end; s = 0, 0.25, ..., 3. */
  inward: readonly number[];
  /** Up, in the same units and at the same contour positions. */
  up: readonly number[];
  /** Strength of negative values relative to positive ones; the reference app is asymmetric. */
  negativeScale: number;
  /** Edits that act on a short stretch of the outline use a narrower band. */
  local: boolean;
};

// Contour parameter s: 0 face side at brow height, 1 face side at the eyes, 2 jaw, 3 chin, mirrored up the
// right side. Profiles follow optical flow measured on the reference retouching app at its slider ends;
// positive values match the end of its slider. Temple and pointed chin had no reference and are designed.
const faceShapeProfiles: Record<FaceShapeControlKey, ContourProfile> = {
  // Positive narrows the face from brow height to the jaw and lifts the jaw slightly.
  faceWidth: {
    inward: [0.5, 0.9, 1.35, 1.6, 1.8, 2.05, 2.4, 2.2, 1.75, 1.35, 0.95, 0.45, 0],
    up: [0, 0, 0, 0, 0, 0.5, 1.1, 1.4, 1.6, 1.4, 1, 0.3, 0],
    negativeScale: 1,
    local: false,
  },
  // Positive draws the lower outline in toward the center and lifts the chin.
  smallFace: {
    inward: [0.1, 0.15, 0.3, 0.55, 1, 1.4, 1.85, 2.35, 2.45, 2.3, 1.7, 0.75, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0.12, 0.55, 1.1],
    negativeScale: 0.6,
    local: false,
  },
  // Positive fills the temples outward between brow and eye height.
  temple: {
    inward: [-1, -1.4, -1.6, -1.4, -0.9, -0.4, -0.1, 0, 0, 0, 0, 0, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    negativeScale: 1,
    local: true,
  },
  // Positive narrows the cheekbones just below the eyes.
  cheekbone: {
    inward: [0, 0.05, 0.35, 1, 1.65, 1.8, 1.45, 0.65, 0.15, 0, 0, 0, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    negativeScale: 0.7,
    local: true,
  },
  // Positive shortens the chin by lifting its tip.
  chinLength: {
    inward: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0.3, 1.9, 2.8, 3.1],
    negativeScale: 0.73,
    local: true,
  },
  // Positive narrows the chin sides and draws the tip down into a point.
  chinPoint: {
    inward: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0.3, 1, 0.9, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, -0.2, -0.6, -1],
    negativeScale: 1,
    local: true,
  },
  // Positive lifts and narrows the lower jaw between the jaw corner and the chin.
  vLine: {
    inward: [0, 0, 0, 0, 0, 0, 0, 0, 0.25, 0.6, 1.1, 0.45, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 0.3, 1.7, 2.45, 0.95, 0.1, 0],
    negativeScale: 1,
    local: true,
  },
  // Positive pulls the jaw corners in and up.
  jawAngle: {
    inward: [0, 0, 0, 0, 0, 0, 0, 0.55, 1.15, 1.35, 0.55, 0, 0],
    up: [0, 0, 0, 0, 0, 0, 0, 1.25, 2, 0.95, 0.25, 0, 0],
    negativeScale: 0.56,
    local: true,
  },
};

/** Stored value at the default slider end; the profiles above are calibrated there. */
const faceShapeSliderEnd = 0.2;
// Displacement fades from the outline over this fraction of the face width, into the face and out into hair.
const broadBand = 0.3;
const localBand = 0.2;

/**
 * Face-shape edit as displacement profiles along the face outline. The outline runs, image left first, from
 * the face side at brow height through the face side beside the eye, the jaw and the chin, and back up.
 */
export type FaceShapeTransform = {
  contour: LandmarkPoint[];
  /** Profiles in fractions of the face width, one per band width. */
  bands: { band: number; inward: number[]; up: number[] }[];
};

export function createFaceShapeTransform(recipe: EditRecipe, landmarks: ManualLandmarks): FaceShapeTransform | null {
  const bands = [false, true].map((local) => {
    const inward = new Array<number>(13).fill(0);
    const up = new Array<number>(13).fill(0);
    let active = false;
    for (const [key, profile] of Object.entries(faceShapeProfiles) as [FaceShapeControlKey, ContourProfile][]) {
      const value = recipe.face[key];
      if (profile.local !== local || value === 0) continue;
      active = true;
      const scale = (value / faceShapeSliderEnd) * (value < 0 ? profile.negativeScale : 1) / 100;
      profile.inward.forEach((amount, index) => { inward[index] += amount * scale; });
      profile.up.forEach((amount, index) => { up[index] += amount * scale; });
    }
    return active ? { band: local ? localBand : broadBand, inward, up } : null;
  }).filter((band) => band !== null);

  return bands.length > 0 ? { contour: faceContour(landmarks), bands } : null;
}

/** Face outline control points estimated from the sparse anime landmarks, image left first. */
export function faceContour(landmarks: ManualLandmarks): LandmarkPoint[] {
  const leftIsImageLeft = landmarks.leftEye.x <= landmarks.rightEye.x;
  const eyeDistance = Math.max(0.02, Math.abs(landmarks.rightEye.x - landmarks.leftEye.x));
  const brows = resolveBrowLandmarks(landmarks);
  const side = (key: "left" | "right", outward: -1 | 1) => {
    const eye = key === "left" ? landmarks.leftEye : landmarks.rightEye;
    const region = landmarks.eyeRegions?.[key];
    // Detected eye regions span about 1.36 eye half-widths. The face edge sits just beyond the outer corner.
    const halfWidth = region ? region.radiusX / 1.36 : eyeDistance * 0.23;
    const brow = brows[key];
    const browY = (brow.inner.y + brow.outer.y + brow.peak.y) / 3;
    const eyeSide = { x: eye.x + outward * halfWidth * 1.12, y: eye.y };
    return { top: { x: eyeSide.x - outward * halfWidth * 0.3, y: browY }, eyeSide };
  };
  const imageLeft = side(leftIsImageLeft ? "left" : "right", -1);
  const imageRight = side(leftIsImageLeft ? "right" : "left", 1);
  const [jawImageLeft, jawImageRight] = landmarks.jawLeft.x <= landmarks.jawRight.x
    ? [landmarks.jawLeft, landmarks.jawRight]
    : [landmarks.jawRight, landmarks.jawLeft];

  return [imageLeft.top, imageLeft.eyeSide, jawImageLeft, landmarks.chin, jawImageRight, imageRight.eyeSide, imageRight.top];
}

function profileAt(table: readonly number[], s: number) {
  const position = Math.min(table.length - 1, Math.max(0, s / 0.25));
  const index = Math.min(table.length - 2, Math.floor(position));
  const amount = position - index;
  return table[index] * (1 - amount) + table[index + 1] * amount;
}

/** Catmull-Rom samples through the control points, each with its contour parameter. */
function sampleContour(points: readonly { x: number; y: number }[], perSegment: number) {
  const padded = [points[0], ...points, points[points.length - 1]];
  const samples: { s: number; x: number; y: number }[] = [];
  for (let segment = 0; segment < points.length - 1; segment += 1) {
    const [p0, p1, p2, p3] = padded.slice(segment, segment + 4);
    for (let step = 0; step < perSegment; step += 1) {
      const t = step / perSegment;
      const t2 = t * t;
      const t3 = t2 * t;
      const at = (a: number, b: number, c: number, d: number) =>
        0.5 * (2 * b + (-a + c) * t + (2 * a - 5 * b + 4 * c - d) * t2 + (-a + 3 * b - 3 * c + d) * t3);
      samples.push({ s: segment + t, x: at(p0.x, p1.x, p2.x, p3.x), y: at(p0.y, p1.y, p2.y, p3.y) });
    }
  }
  const last = points[points.length - 1];
  samples.push({ s: points.length - 1, x: last.x, y: last.y });
  return samples;
}

/**
 * Pixel displacement for a face-shape transform on an image of the given pixel size. Along the outline the
 * displacement follows the profiles; it fades with distance from the outline over the band width.
 */
export function createFaceShapeDeformer(transform: FaceShapeTransform, width: number, height: number) {
  const control = transform.contour.map((point) => ({ x: point.x * width, y: point.y * height }));
  const faceWidth = Math.max(1, Math.hypot(control[5].x - control[1].x, control[5].y - control[1].y));
  const samples = sampleContour(control, 12);
  const bands = transform.bands.map((band) => {
    const reach = band.band * faceWidth;
    const offsets = samples.map((sample) => {
      const mirrored = sample.s <= 3 ? sample.s : 6 - sample.s;
      const towardCenter = sample.s < 3 ? 1 : -1;
      return {
        x: towardCenter * profileAt(band.inward, mirrored) * faceWidth,
        y: -profileAt(band.up, mirrored) * faceWidth,
      };
    });
    const xs = samples.map((sample) => sample.x);
    const ys = samples.map((sample) => sample.y);
    return {
      reach,
      // Averaging width along the outline; narrower than the reach so local profiles stay local.
      sigmaSquared: (reach * 0.35) ** 2,
      offsets,
      bounds: {
        minX: Math.min(...xs) - reach, maxX: Math.max(...xs) + reach,
        minY: Math.min(...ys) - reach, maxY: Math.max(...ys) + reach,
      },
    };
  });

  return (x: number, y: number) => {
    let offsetX = 0;
    let offsetY = 0;
    for (const band of bands) {
      if (x < band.bounds.minX || x > band.bounds.maxX || y < band.bounds.minY || y > band.bounds.maxY) continue;

      let nearestSquared = Infinity;
      let weightSum = 0;
      let weightedX = 0;
      let weightedY = 0;
      for (let index = 0; index < samples.length; index += 1) {
        const distanceSquared = (x - samples[index].x) ** 2 + (y - samples[index].y) ** 2;
        nearestSquared = Math.min(nearestSquared, distanceSquared);
        const weight = Math.exp(-distanceSquared / band.sigmaSquared);
        weightSum += weight;
        weightedX += band.offsets[index].x * weight;
        weightedY += band.offsets[index].y * weight;
      }
      const envelope = flatFalloff(Math.sqrt(nearestSquared) / band.reach);
      if (envelope === 0 || weightSum === 0) continue;
      offsetX += (weightedX / weightSum) * envelope;
      offsetY += (weightedY / weightSum) * envelope;
    }
    return { x: offsetX, y: offsetY };
  };
}

/** Proportion edits change the spacing between features, so they deliberately move them. */
export function createProportionStrokes(recipe: EditRecipe, landmarks: ManualLandmarks): LiquifyStroke[] {
  const strokes: LiquifyStroke[] = [];
  const { face } = recipe;
  const eyeY = (landmarks.leftEye.y + landmarks.rightEye.y) / 2;

  if (face.faceLength !== 0) {
    const foreheadY = Math.max(0, eyeY - Math.abs(landmarks.chin.y - eyeY) * 0.7);
    const grow = face.faceLength > 0;
    pushStroke(strokes, { x: landmarks.chin.x, y: foreheadY }, grow ? "push-up" : "push-down", face.faceLength * 0.3, 128);
    pushStroke(strokes, landmarks.jawLeft, grow ? "push-down" : "push-up", face.faceLength * 0.34, 132);
    pushStroke(strokes, landmarks.jawRight, grow ? "push-down" : "push-up", face.faceLength * 0.34, 132);
    pushStroke(strokes, landmarks.jawLeft, grow ? "push-right" : "push-left", face.faceLength * 0.14, 120);
    pushStroke(strokes, landmarks.jawRight, grow ? "push-left" : "push-right", face.faceLength * 0.14, 120);
    pushStroke(strokes, landmarks.chin, grow ? "push-down" : "push-up", face.faceLength * 0.36, 140);
  }

  if (face.midFaceLength !== 0) {
    const mouthY = landmarks.mouthCenter.y;
    const midFaceY = (eyeY + mouthY) / 2;
    const cheekY = (landmarks.jawLeft.y + eyeY) / 2;
    const lowerCheekY = (cheekY + mouthY) / 2;
    const mode = face.midFaceLength > 0 ? "push-down" : "push-up";
    const cheekLeftX = (landmarks.jawLeft.x + landmarks.leftEye.x) / 2;
    const cheekRightX = (landmarks.jawRight.x + landmarks.rightEye.x) / 2;

    pushStroke(strokes, { x: landmarks.mouthCenter.x, y: midFaceY }, mode, face.midFaceLength * 0.32, 96);
    pushStroke(strokes, landmarks.mouthCenter, mode, face.midFaceLength * 0.62, 112);
    pushStroke(strokes, { x: cheekLeftX, y: lowerCheekY }, mode, face.midFaceLength * 0.28, 104);
    pushStroke(strokes, { x: cheekRightX, y: lowerCheekY }, mode, face.midFaceLength * 0.28, 104);
  }

  return strokes;
}

/** Eyes, brows, nose and mouth, padded so lashes, brow ends and lips are covered. */
export function createFeatureProtectionZones(landmarks: ManualLandmarks): FeatureProtectionZone[] {
  const eyeDistance = Math.max(0.02, Math.abs(landmarks.rightEye.x - landmarks.leftEye.x));
  const eyeY = (landmarks.leftEye.y + landmarks.rightEye.y) / 2;
  const eyeToMouth = Math.max(0.03, landmarks.mouthCenter.y - eyeY);
  // Zones hug each feature, because anime eyes and brow tails nearly touch the face outline: a wide
  // zone would freeze the cheekbones and temples. Feature centers stay fixed; only the outer eye tip
  // follows the outline a little.
  const eyeZone = (eye: LandmarkPoint, side: "left" | "right"): FeatureProtectionZone => {
    const region = landmarks.eyeRegions?.[side];
    // Detected eye regions span about 1.36 eye half-widths and reach from lash line to lower lid.
    const halfWidth = region ? region.radiusX / 1.36 : eyeDistance * 0.23;
    const halfHeight = region ? Math.max(region.radiusTopY, region.radiusBottomY) : eyeToMouth * 0.3;
    return { centerX: eye.x, centerY: eye.y, radiusX: halfWidth * 0.75, radiusY: halfHeight, fadeOut: 1.7 };
  };
  const brows = resolveBrowLandmarks(landmarks);
  const browZone = (brow: (typeof brows)["left"]): FeatureProtectionZone => ({
    centerX: (brow.inner.x + brow.outer.x) / 2,
    centerY: (brow.inner.y + brow.outer.y + brow.peak.y) / 3,
    radiusX: Math.abs(brow.outer.x - brow.inner.x) / 2 + eyeDistance * 0.04,
    radiusY: Math.abs(brow.outer.y - brow.inner.y) / 2 + eyeToMouth * 0.12,
    fadeOut: 1.5,
  });
  const mouthHalfWidth = Math.abs(landmarks.mouthRight.x - landmarks.mouthLeft.x) / 2;

  return [
    eyeZone(landmarks.leftEye, "left"),
    eyeZone(landmarks.rightEye, "right"),
    browZone(brows.left),
    browZone(brows.right),
    {
      centerX: (landmarks.leftEye.x + landmarks.rightEye.x) / 2,
      // Anime noses are small and sit high, about a sixth of the way from the eyes to the mouth.
      centerY: eyeY + eyeToMouth * 0.3,
      radiusX: eyeDistance * 0.14,
      radiusY: eyeToMouth * 0.3,
      fadeOut: 2,
    },
    {
      centerX: landmarks.mouthCenter.x,
      centerY: landmarks.mouthCenter.y,
      // Tight around the lips: anime chins sit close below the mouth and need room to move.
      radiusX: Math.max(mouthHalfWidth * 1.35, eyeDistance * 0.13),
      radiusY: Math.max(mouthHalfWidth * 0.8, (landmarks.chin.y - landmarks.mouthCenter.y) * 0.25),
      // A wide fade spreads chin and jaw edits over the whole lower face instead of creasing below the lips.
      fadeOut: 2.6,
    },
  ];
}

/** 1 where a feature must stay fixed, 0 where face-shape edits apply in full. */
export function getFeatureProtection(zones: readonly FeatureProtectionZone[], x: number, y: number) {
  let protection = 0;
  for (const zone of zones) {
    const distance = Math.hypot((x - zone.centerX) / zone.radiusX, (y - zone.centerY) / zone.radiusY);
    protection = Math.max(protection, coreFalloff(distance, zone.fadeOut));
    if (protection >= 1) return 1;
  }
  return protection;
}
