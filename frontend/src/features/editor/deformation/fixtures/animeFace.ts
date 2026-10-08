import type { ManualLandmarks } from "../landmarks";

/** Landmarks the anime HRNet detector finds on an 883 x 1291 front-facing portrait with brows under the bangs. */
export const animeFaceSize = { height: 1291, width: 883 } as const;

const point = (x: number, y: number) => ({ x: x / animeFaceSize.width, y: y / animeFaceSize.height });

export const animeFaceLandmarks: ManualLandmarks = {
  leftEye: point(308, 686),
  rightEye: point(598, 662),
  chin: point(468, 903),
  jawLeft: point(282, 833),
  jawRight: point(641, 803),
  mouthCenter: point(461, 810),
  mouthLeft: point(425, 820),
  mouthRight: point(499, 801),
  eyeRegions: {
    left: { radiusX: 0.1068, radiusY: 0.0386, radiusTopY: 0.0386, radiusBottomY: 0.0386 },
    right: { radiusX: 0.1, radiusY: 0.0363, radiusTopY: 0.0363, radiusBottomY: 0.0363 },
  },
  brows: {
    left: { inner: point(347, 560), outer: point(234, 560), peak: point(295, 565) },
    right: { inner: point(507, 552), outer: point(624, 525), peak: point(564, 538) },
  },
};

/** Feature centers in image pixels of the portrait; the nose is the detector nose point. */
export const animeFaceFeatures = {
  leftEye: [308, 686],
  rightEye: [598, 662],
  leftBrow: [291, 562],
  rightBrow: [566, 538],
  nose: [455, 694],
  mouth: [461, 810],
} as const;
