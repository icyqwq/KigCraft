import type {AnimeLandmarkDetection} from "../deformation/animeLandmarkDetector";

// The heavy detector and its shared model sessions are loaded only after an
// editing action needs them. Per-editor cancellation never tears down shared models.
let implementation: Promise<typeof import("../deformation/animeLandmarkDetector")> | undefined;
export async function detectEditorLandmarks(image: HTMLImageElement, signal: AbortSignal): Promise<AnimeLandmarkDetection | null> {
  signal.throwIfAborted();
  implementation ??= import("../deformation/animeLandmarkDetector").catch(error => {
    implementation = undefined;
    throw error;
  });
  const detector = await implementation;
  signal.throwIfAborted();
  const result = await detector.detectAnimeLandmarks(image, signal, true);
  signal.throwIfAborted();
  return result;
}
