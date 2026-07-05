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

const mediaPipeWasmUrl = "/mediapipe-wasm";
const mediaPipeFaceModelUrl = "/models/blaze_face_short_range.tflite";
const hrnetModelUrl = "/models/anime-face-hrnetv2-int8.onnx";
const modelSize = 256;
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

function pickFaceBox(detections: readonly Detection[]) {
  return detections
    .map((detection) => ({ box: detectionToBox(detection), score: detectionScore(detection) }))
    .filter((entry): entry is { box: FaceBox; score: number } => Boolean(entry.box))
    .sort((left, right) => right.score * right.box.width * right.box.height - left.score * left.box.width * left.box.height)[0]
    ?? null;
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

export function detectAnimeLandmarks(image: HTMLImageElement): Promise<AnimeLandmarkDetection | null> {
  return detectAnimeLandmarksWithModel(image, hrnetModelUrl);
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
): Promise<AnimeLandmarkDetection | null> {
  const startedAt = performance.now();
  const { height, width } = getImageSize(image);
  if (width <= 1 || height <= 1) return null;

  const faceDetectorTask = getFaceDetector();
  const ortTask = getOrt();
  const hrnetRuntimeTask = getHrnetSession(modelUrl);
  let faceEntry: { box: FaceBox; score: number } | null = null;
  try {
    const faceDetector = await faceDetectorTask;
    faceEntry = pickFaceBox(faceDetector.detect(image).detections);
  } catch (error: unknown) {
    console.warn("Anime face detector failed, using centered landmark crop", error);
  }
  faceEntry ??= createFallbackFaceBox(width, height);
  const faceBox = faceEntry.box;

  const hrnetBox = expandFaceBox(faceBox, width, height);
  const [ort, hrnetRuntime] = await Promise.all([ortTask, hrnetRuntimeTask]);
  const { session: hrnetSession } = hrnetRuntime;
  const result = await runHrnetSession(hrnetSession, {
    [hrnetSession.inputNames[0]]: preprocessCrop(image, hrnetBox, ort),
  });
  const heatmaps = result[hrnetSession.outputNames[0]];
  const data = heatmaps.data;

  if (!(data instanceof Float32Array)) return null;

  const points = decodeHrnetHeatmaps(data, hrnetBox);
  const controls = mapAnimePointsToManualLandmarks(points, width, height, faceBox);
  if (!controls) return null;

  return {
    controls,
    debug: {
      detectionMs: Math.round(performance.now() - startedAt),
      faceBox: { ...faceBox, score: faceEntry.score },
      hrnetBox,
      hrnetProvider: hrnetRuntime.provider,
      imageHeight: height,
      imageWidth: width,
      points: points.map((point, index) => ({ ...point, index })),
    },
    details: normalizeAnimeDetailPoints(points, width, height),
  };
}

function runHrnetSession(
  session: ort.InferenceSession,
  feeds: Parameters<ort.InferenceSession["run"]>[0],
) {
  const run = hrnetRunQueue.then(() => session.run(feeds));
  hrnetRunQueue = run.catch(() => undefined);
  return run;
}
