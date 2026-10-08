export type LandmarkPoint = {
  x: number;
  y: number;
};

export type EyeRegion = {
  radiusBottomY: number;
  radiusTopY: number;
  radiusX: number;
  radiusY: number;
};

/** Brow line from the end nearest the face center to the tail; peak is the highest middle point. */
export type BrowLandmark = {
  inner: LandmarkPoint;
  outer: LandmarkPoint;
  peak: LandmarkPoint;
};

export type ManualLandmarks = {
  leftEye: LandmarkPoint;
  rightEye: LandmarkPoint;
  chin: LandmarkPoint;
  jawLeft: LandmarkPoint;
  jawRight: LandmarkPoint;
  mouthCenter: LandmarkPoint;
  mouthLeft: LandmarkPoint;
  mouthRight: LandmarkPoint;
  eyeRegions?: {
    left: EyeRegion;
    right: EyeRegion;
  };
  brows?: {
    left: BrowLandmark;
    right: BrowLandmark;
  };
};

export type BrowLandmarkKey =
  | "leftBrowInner"
  | "leftBrowPeak"
  | "leftBrowOuter"
  | "rightBrowInner"
  | "rightBrowPeak"
  | "rightBrowOuter";

export type ManualLandmarkKey =
  | "leftEye"
  | "rightEye"
  | "chin"
  | "jawLeft"
  | "jawRight"
  | "mouthCenter"
  | "mouthLeft"
  | "mouthRight"
  | BrowLandmarkKey;

const browLandmarkParts: Record<BrowLandmarkKey, ["left" | "right", keyof BrowLandmark]> = {
  leftBrowInner: ["left", "inner"],
  leftBrowPeak: ["left", "peak"],
  leftBrowOuter: ["left", "outer"],
  rightBrowInner: ["right", "inner"],
  rightBrowPeak: ["right", "peak"],
  rightBrowOuter: ["right", "outer"],
};

function isBrowLandmarkKey(key: ManualLandmarkKey): key is BrowLandmarkKey {
  return key in browLandmarkParts;
}

/** A landmark point by key; brow points fall back to the eye-based estimate. */
export function getManualLandmarkPoint(landmarks: ManualLandmarks, key: ManualLandmarkKey): LandmarkPoint {
  if (!isBrowLandmarkKey(key)) return landmarks[key];
  const [side, part] = browLandmarkParts[key];
  return resolveBrowLandmarks(landmarks)[side][part];
}

/** Moves one landmark. Moving an estimated brow point keeps the other estimated brow points where they show. */
export function setManualLandmarkPoint(landmarks: ManualLandmarks, key: ManualLandmarkKey, point: LandmarkPoint): ManualLandmarks {
  if (!isBrowLandmarkKey(key)) return { ...landmarks, [key]: point };
  const [side, part] = browLandmarkParts[key];
  const brows = resolveBrowLandmarks(landmarks);
  return { ...landmarks, brows: { ...brows, [side]: { ...brows[side], [part]: point } } };
}

export type DeformationRegions = {
  eyeCenter: LandmarkPoint;
  faceCenter: LandmarkPoint;
  jawWidth: number;
  eyeDistance: number;
};

function toPoint(width: number, height: number, x: number, y: number): LandmarkPoint {
  return {
    x: width * x,
    y: height * y,
  };
}

function normalizePoint(point: LandmarkPoint, width: number, height: number): LandmarkPoint {
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);

  return {
    x: Number((point.x / safeWidth).toFixed(4)),
    y: Number((point.y / safeHeight).toFixed(4)),
  };
}

export function createDefaultLandmarks(width: number, height: number): ManualLandmarks {
  return {
    leftEye: toPoint(width, height, 0.42, 0.42),
    rightEye: toPoint(width, height, 0.58, 0.42),
    chin: toPoint(width, height, 0.5, 0.7),
    jawLeft: toPoint(width, height, 0.39, 0.6),
    jawRight: toPoint(width, height, 0.61, 0.6),
    mouthCenter: toPoint(width, height, 0.5, 0.57),
    mouthLeft: toPoint(width, height, 0.47, 0.57),
    mouthRight: toPoint(width, height, 0.53, 0.57),
  };
}

export function completeLandmarks(landmarks: Partial<ManualLandmarks> | ManualLandmarks): ManualLandmarks {
  const defaults = createDefaultLandmarks(1, 1);
  const leftEye = landmarks.leftEye ?? defaults.leftEye;
  const rightEye = landmarks.rightEye ?? defaults.rightEye;
  const chin = landmarks.chin ?? defaults.chin;
  const jawLeft = landmarks.jawLeft ?? defaults.jawLeft;
  const jawRight = landmarks.jawRight ?? defaults.jawRight;
  const eyeCenter = {
    x: (leftEye.x + rightEye.x) / 2,
    y: (leftEye.y + rightEye.y) / 2,
  };
  const faceHeight = Math.max(0.1, Math.abs(chin.y - eyeCenter.y));
  const jawWidth = Math.max(0.08, Math.abs(jawRight.x - jawLeft.x));
  const mouthCenter = landmarks.mouthCenter ?? {
    x: (eyeCenter.x + chin.x) / 2,
    y: eyeCenter.y + faceHeight * 0.52,
  };
  const mouthHalfWidth = Math.max(0.025, jawWidth * 0.18);

  return {
    leftEye,
    rightEye,
    chin,
    jawLeft,
    jawRight,
    mouthCenter,
    mouthLeft: landmarks.mouthLeft ?? { x: mouthCenter.x - mouthHalfWidth, y: mouthCenter.y },
    mouthRight: landmarks.mouthRight ?? { x: mouthCenter.x + mouthHalfWidth, y: mouthCenter.y },
    eyeRegions: landmarks.eyeRegions,
    brows: landmarks.brows,
  };
}

export function normalizeLandmarks(landmarks: ManualLandmarks, width: number, height: number): ManualLandmarks {
  const completed = completeLandmarks(landmarks);

  return {
    leftEye: normalizePoint(completed.leftEye, width, height),
    rightEye: normalizePoint(completed.rightEye, width, height),
    chin: normalizePoint(completed.chin, width, height),
    jawLeft: normalizePoint(completed.jawLeft, width, height),
    jawRight: normalizePoint(completed.jawRight, width, height),
    mouthCenter: normalizePoint(completed.mouthCenter, width, height),
    mouthLeft: normalizePoint(completed.mouthLeft, width, height),
    mouthRight: normalizePoint(completed.mouthRight, width, height),
    eyeRegions: completed.eyeRegions,
    brows: completed.brows,
  };
}

/**
 * Detected brows, or brows estimated above the eyes for landmarks without them.
 * Anime brows usually sit a little under half the eye distance above the eye center.
 */
export function resolveBrowLandmarks(landmarks: ManualLandmarks): { left: BrowLandmark; right: BrowLandmark } {
  if (landmarks.brows) return landmarks.brows;

  const eyeDistance = Math.max(0.02, Math.abs(landmarks.rightEye.x - landmarks.leftEye.x));
  const estimate = (eye: LandmarkPoint, otherEye: LandmarkPoint): BrowLandmark => {
    const inward = eye.x <= otherEye.x ? 1 : -1;
    const y = eye.y - eyeDistance * 0.45;
    const halfLength = eyeDistance * 0.2;
    const centerX = eye.x + inward * eyeDistance * 0.04;

    return {
      inner: { x: centerX + inward * halfLength, y },
      outer: { x: centerX - inward * halfLength, y },
      peak: { x: centerX - inward * halfLength * 0.3, y: y - eyeDistance * 0.02 },
    };
  };

  return {
    left: estimate(landmarks.leftEye, landmarks.rightEye),
    right: estimate(landmarks.rightEye, landmarks.leftEye),
  };
}

export function deriveDeformationRegions(landmarks: ManualLandmarks): DeformationRegions {
  const eyeCenter = {
    x: (landmarks.leftEye.x + landmarks.rightEye.x) / 2,
    y: (landmarks.leftEye.y + landmarks.rightEye.y) / 2,
  };
  const faceCenter = {
    x: (eyeCenter.x + landmarks.chin.x) / 2,
    y: (eyeCenter.y + landmarks.chin.y) / 2,
  };

  return {
    eyeCenter,
    faceCenter,
    jawWidth: Math.abs(landmarks.jawRight.x - landmarks.jawLeft.x),
    eyeDistance: Math.abs(landmarks.rightEye.x - landmarks.leftEye.x),
  };
}

