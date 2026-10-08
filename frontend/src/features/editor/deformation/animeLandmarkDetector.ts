import { publicUrl } from "../../../ui/publicUrl";
import { FaceDetector, FilesetResolver, type Detection } from "@mediapipe/tasks-vision";
import * as ort from "onnxruntime-web/wasm";
import ortWasmThreadedUrl from "../../../../node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm?url";
import type { LandmarkPoint, ManualLandmarks } from "./landmarks";
import {
  decodeHrnetHeatmaps,
  expandFaceBox,
  mapAnimePointsToManualLandmarks,
  normalizeAnimeDetailPoints,
  type DetectedPoint,
  type FaceBox,
} from "./animeLandmarkMapping";

const mediaPipeWasmUrl = publicUrl("/mediapipe-wasm");
const mediaPipeFaceModelUrl = publicUrl("/models/blaze_face_short_range.tflite");
const hrnetModelUrl = publicUrl("/models/anime-face-hrnetv2-int8.onnx");
const modelSize = 256;
/**
 * Single-face mode does not trust the face detector's own ranking: on flat illustrations it prefers hair. Several boxes are
 * tried and the one the landmark model fits best wins, provided it fits well enough.
 */
const maxFaceCandidates = 4;
const minLandmarkQuality = 0.45;
const ambiguousQuality = 0.8;
const imageNetMean = [0.485, 0.456, 0.406] as const;
const imageNetStd = [0.229, 0.224, 0.225] as const;

let faceDetectorPromise: Promise<FaceDetector> | null = null;
let ortPromise: Promise<typeof ort> | null = null;
const hrnetSessionPromises = new Map<string, Promise<HrnetRuntime>>();
let hrnetRunQueue: Promise<unknown> = Promise.resolve();

type OrtModule = typeof ort;
type HrnetProvider = "wasm";

type HrnetRuntime = {
  provider: HrnetProvider;
  session: ort.InferenceSession;
};

export type AnimeLandmarkDebugBox = FaceBox & {
  score?: number;
};

export type AnimeLandmarkDebugInfo = {
  detectionMs: number;
  faceBox: AnimeLandmarkDebugBox;
  hrnetBox: AnimeLandmarkDebugBox;
  hrnetProvider: HrnetProvider;
  imageHeight: number;
  imageWidth: number;
  points: Array<DetectedPoint & { index: number }>;
  /** Every face box that was tried in single-face mode, with how well the landmark model fitted it. */
  candidates?: Array<{ box: FaceBox; detectorScore: number; quality: number }>;
};

export type AnimeLandmarkDetection = {
  controls: ManualLandmarks;
  debug: AnimeLandmarkDebugInfo;
  details: LandmarkPoint[];
};

export type AnimeFaceBoxDetection = {
  box: FaceBox;
  imageHeight: number;
  imageWidth: number;
  score: number;
  usedFallback: boolean;
};

function getImageSize(image: HTMLImageElement) {
  return {
    height: image.naturalHeight || image.height || 1,
    width: image.naturalWidth || image.width || 1,
  };
}

function detectionScore(detection: Detection) {
  return detection.categories?.[0]?.score ?? 0;
}

function detectionToBox(detection: Detection): FaceBox | null {
  const box = detection.boundingBox;
  if (!box || box.width <= 0 || box.height <= 0) return null;

  return {
    height: box.height,
    width: box.width,
    x: box.originX,
    y: box.originY,
  };
}

function rankFaceBoxes(detections: readonly Detection[]) {
  return detections
    .map((detection) => ({ box: detectionToBox(detection), score: detectionScore(detection) }))
    .filter((entry): entry is { box: FaceBox; score: number } => Boolean(entry.box))
    .sort((left, right) => right.score * right.box.width * right.box.height - left.score * left.box.width * left.box.height);
}

function pickFaceBox(detections: readonly Detection[]) {
  return rankFaceBoxes(detections)[0] ?? null;
}

function overlap(a: FaceBox, b: FaceBox) {
  const width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
  const height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
  if (width <= 0 || height <= 0) return 0;
  const shared = width * height;
  return shared / Math.min(a.width * a.height, b.width * b.height);
}

/** Drop boxes that mostly lie inside a better ranked one: the detector often reports one face several times. */
function distinctFaceBoxes(ranked: ReturnType<typeof rankFaceBoxes>) {
  const kept: typeof ranked = [];
  for (const entry of ranked) {
    if (kept.length >= maxFaceCandidates) break;
    if (kept.every((other) => overlap(entry.box, other.box) < 0.6)) kept.push(entry);
  }
  return kept;
}

function createFallbackFaceBox(imageWidth: number, imageHeight: number): { box: FaceBox; score: number } {
  const safeWidth = Math.max(1, imageWidth);
  const safeHeight = Math.max(1, imageHeight);
  const size = Math.min(safeWidth * 0.78, safeHeight * 0.68);

  return {
    box: {
      height: size,
      width: size,
      x: (safeWidth - size) / 2,
      y: Math.max(0, safeHeight * 0.43 - size / 2),
    },
    score: 0,
  };
}

async function getFaceDetector() {
  faceDetectorPromise ??= (async () => {
    const vision = await FilesetResolver.forVisionTasks(mediaPipeWasmUrl);
    return FaceDetector.createFromOptions(vision, {
      baseOptions: {
        delegate: "CPU",
        modelAssetPath: mediaPipeFaceModelUrl,
      },
      minDetectionConfidence: 0.15,
      runningMode: "IMAGE",
    });
  })().catch((error: unknown) => {
    faceDetectorPromise = null;
    throw error;
  });

  return faceDetectorPromise;
}

async function getOrt() {
  ortPromise ??= Promise.resolve().then(() => {
    ort.env.wasm.numThreads = 1;
    (ort.env.wasm as unknown as { wasmPaths: { wasm: string } }).wasmPaths = {
      wasm: ortWasmThreadedUrl,
    };
    return ort;
  }).catch((error: unknown) => {
    ortPromise = null;
    throw error;
  });

  return ortPromise;
}

async function getHrnetSession(modelUrl = hrnetModelUrl) {
  let sessionPromise = hrnetSessionPromises.get(modelUrl);
  if (!sessionPromise) {
    sessionPromise = (async (): Promise<HrnetRuntime> => {
      const ortRuntime = await getOrt();
      const session = await ortRuntime.InferenceSession.create(modelUrl, {
        executionProviders: ["wasm"],
        graphOptimizationLevel: "all",
      });
      console.info("Anime landmark HRNet initialized with wasm");
      return { provider: "wasm", session };
    })().catch((error: unknown) => {
      hrnetSessionPromises.delete(modelUrl);
      console.warn("Anime landmark HRNet wasm initialization failed", error);
      throw error;
    });
    hrnetSessionPromises.set(modelUrl, sessionPromise);
  }

  return sessionPromise;
}

export async function warmupAnimeLandmarkDetector() {
  await Promise.allSettled([getFaceDetector(), getOrt(), getHrnetSession()]);
}

export const warmupAnimeLandmarkModels = warmupAnimeLandmarkDetector;

function preprocessCrop(image: HTMLImageElement, box: FaceBox, ort: OrtModule) {
  const canvas = document.createElement("canvas");
  canvas.width = modelSize;
  canvas.height = modelSize;

  const context = canvas.getContext("2d", { willReadFrequently: true });
  if (!context) {
    throw new Error("Unable to create landmark crop canvas");
  }

  const { height: imageHeight, width: imageWidth } = getImageSize(image);
  const srcX = Math.max(0, box.x);
  const srcY = Math.max(0, box.y);
  const srcRight = Math.min(imageWidth, box.x + box.width);
  const srcBottom = Math.min(imageHeight, box.y + box.height);
  const srcWidth = Math.max(1, srcRight - srcX);
  const srcHeight = Math.max(1, srcBottom - srcY);
  const dstX = ((srcX - box.x) / box.width) * modelSize;
  const dstY = ((srcY - box.y) / box.height) * modelSize;
  const dstWidth = (srcWidth / box.width) * modelSize;
  const dstHeight = (srcHeight / box.height) * modelSize;

  context.fillStyle = "#fff";
  context.fillRect(0, 0, modelSize, modelSize);
  context.drawImage(image, srcX, srcY, srcWidth, srcHeight, dstX, dstY, dstWidth, dstHeight);

  const pixels = context.getImageData(0, 0, modelSize, modelSize).data;
  const input = new Float32Array(3 * modelSize * modelSize);

  for (let y = 0; y < modelSize; y += 1) {
    for (let x = 0; x < modelSize; x += 1) {
      const pixelOffset = (y * modelSize + x) * 4;
      const tensorOffset = y * modelSize + x;
      const rgb = [pixels[pixelOffset] / 255, pixels[pixelOffset + 1] / 255, pixels[pixelOffset + 2] / 255];

      for (let channel = 0; channel < 3; channel += 1) {
        input[channel * modelSize * modelSize + tensorOffset] = (rgb[channel] - imageNetMean[channel]) / imageNetStd[channel];
      }
    }
  }

  return new ort.Tensor("float32", input, [1, 3, modelSize, modelSize]);
}

export function detectAnimeLandmarks(image: HTMLImageElement, signal?: AbortSignal, requireSingleFace = false): Promise<AnimeLandmarkDetection | null> {
  return detectAnimeLandmarksWithModel(image, hrnetModelUrl, signal, requireSingleFace);
}

export async function detectAnimeFaceBox(image: HTMLImageElement): Promise<AnimeFaceBoxDetection | null> {
  const { height, width } = getImageSize(image);
  if (width <= 1 || height <= 1) return null;

  let faceEntry: { box: FaceBox; score: number } | null = null;
  try {
    const faceDetector = await getFaceDetector();
    faceEntry = pickFaceBox(faceDetector.detect(image).detections);
  } catch (error: unknown) {
    console.warn("Anime face detector failed, using centered face crop", error);
  }

  const usedFallback = !faceEntry;
  faceEntry ??= createFallbackFaceBox(width, height);
  return {
    box: faceEntry.box,
    imageHeight: height,
    imageWidth: width,
    score: faceEntry.score,
    usedFallback,
  };
}

export async function detectAnimeLandmarksWithModel(
  image: HTMLImageElement,
  modelUrl = hrnetModelUrl,
  signal?: AbortSignal,
  requireSingleFace = false,
): Promise<AnimeLandmarkDetection | null> {
  const startedAt = performance.now();
  const { height, width } = getImageSize(image);
  if (width <= 1 || height <= 1) return null;

  signal?.throwIfAborted();
  // Attach rejection handlers to all parallel loads immediately. A failed face
  // detector retains the existing centered-crop fallback; model failure propagates.
  const [faceDetector, ort, hrnetRuntime] = await Promise.all([
    getFaceDetector().catch((error: unknown) => {
      console.warn("Anime face detector failed, using centered landmark crop", error);
      return null;
    }),
    getOrt(),
    getHrnetSession(modelUrl),
  ]);
  signal?.throwIfAborted();
  let ranked: ReturnType<typeof rankFaceBoxes> = [];
  try {
    ranked = faceDetector ? rankFaceBoxes(faceDetector.detect(image).detections) : [];
  } catch (error: unknown) {
    console.warn("Anime face detector failed, using centered landmark crop", error);
  }
  const { session: hrnetSession } = hrnetRuntime;

  async function fit(entry: { box: FaceBox; score: number }) {
    const hrnetBox = expandFaceBox(entry.box, width, height);
    const result = await runHrnetSession(hrnetSession, {
      [hrnetSession.inputNames[0]]: preprocessCrop(image, hrnetBox, ort),
    }, signal);
    signal?.throwIfAborted();
    const data = result[hrnetSession.outputNames[0]].data;
    if (!(data instanceof Float32Array)) return null;
    const points = decodeHrnetHeatmaps(data, hrnetBox);
    const controls = mapAnimePointsToManualLandmarks(points, width, height, entry.box);
    if (!controls) return null;
    const quality = points.reduce((sum, point) => sum + point.score, 0) / Math.max(1, points.length);
    return { box: entry.box, detectorScore: entry.score, hrnetBox, points, controls, quality };
  }

  let chosen: NonNullable<Awaited<ReturnType<typeof fit>>> | null = null;
  let candidates: AnimeLandmarkDebugInfo["candidates"];
  if (!requireSingleFace) {
    chosen = await fit(ranked[0] ?? createFallbackFaceBox(width, height));
  } else {
    const attempts: NonNullable<Awaited<ReturnType<typeof fit>>>[] = [];
    for (const entry of distinctFaceBoxes(ranked)) {
      const attempt = await fit(entry);
      if (attempt) attempts.push(attempt);
    }
    candidates = attempts.map(({ box, detectorScore, quality }) => ({ box, detectorScore, quality }));
    chosen = attempts.reduce<(typeof attempts)[number] | null>((best, attempt) => (!best || attempt.quality > best.quality ? attempt : best), null);
    if (!chosen || chosen.quality < minLandmarkQuality) return null;
    const best = chosen;
    // A second, separate face that fits nearly as well means the picture holds more than one face.
    if (attempts.some((other) => other !== best && overlap(other.box, best.box) < 0.1
        && other.quality >= ambiguousQuality && other.quality >= best.quality * 0.85)) return null;
  }
  if (!chosen) return null;

  return {
    controls: chosen.controls,
    debug: {
      detectionMs: Math.round(performance.now() - startedAt),
      faceBox: { ...chosen.box, score: chosen.detectorScore },
      hrnetBox: chosen.hrnetBox,
      hrnetProvider: hrnetRuntime.provider,
      imageHeight: height,
      imageWidth: width,
      points: chosen.points.map((point, index) => ({ ...point, index })),
      candidates,
    },
    details: normalizeAnimeDetailPoints(chosen.points, width, height),
  };
}

function runHrnetSession(
  session: ort.InferenceSession,
  feeds: Parameters<ort.InferenceSession["run"]>[0],
  signal?: AbortSignal,
) {
  const run = hrnetRunQueue.then(() => { signal?.throwIfAborted(); return session.run(feeds); });
  hrnetRunQueue = run.catch(() => undefined);
  return run;
}
