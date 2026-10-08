// Smooth bump profiles shared by the local feature deformations. Each is 1 at the center and reaches
// zero with zero slope at distance 1, so deformations blend into the untouched image without a seam.

/** Nearly flat in the middle: (1 - r^4)^2. Keeps a feature's shape while it moves. */
export function flatFalloff(normalizedDistance: number) {
  if (normalizedDistance >= 1) return 0;
  const squared = normalizedDistance * normalizedDistance;
  const value = 1 - squared * squared;
  return value * value;
}

/** Peaks in the middle: (1 - r^2)^2. Concentrates the change at the center. */
export function peakedFalloff(normalizedDistance: number) {
  if (normalizedDistance >= 1) return 0;
  const value = 1 - normalizedDistance * normalizedDistance;
  return value * value;
}

// Maximum of t * (1 - t^2)^2, reached at t = 1 / sqrt(5).
const peakedRampMax = 0.2862;

/** Rises from 0 to 1 at t = 1 / sqrt(5), then returns to 0 at t = 1. */
export function peakedRamp(t: number) {
  if (t <= 0 || t >= 1) return 0;
  const value = 1 - t * t;
  return (t * value * value) / peakedRampMax;
}

/** 1 inside the core, smoothly 0 at `outer` (in core-radius units). */
export function coreFalloff(normalizedDistance: number, outer: number) {
  if (normalizedDistance <= 1) return 1;
  if (normalizedDistance >= outer) return 0;
  const t = (outer - normalizedDistance) / (outer - 1);
  return t * t * (3 - 2 * t);
}
