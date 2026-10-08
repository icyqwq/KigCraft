import type { ReferenceAttachment } from "../references/attachments";
import type { EditRecipe } from "./deformation/recipe";

export type LocalEditMode = "preserve" | "lock_outside";

export type LocalMaskMode = "brush" | "erase";

export type LocalMaskPoint = {
  x: number;
  y: number;
};

export type LocalMaskStroke = {
  id: string;
  mode: LocalMaskMode;
  points: LocalMaskPoint[];
  radius: number;
};

export type EditorLocalGeneratePayload = {
  references?: ReferenceAttachment[];
  localEditMode?: LocalEditMode;
  baseImageBlob: Blob;
  editNote: string;
  maskImageBlob: Blob;
  recipe: EditRecipe;
  selectedReferenceKeys: string[];
  uploadedReferences: Array<{ description: string; file: File }>;
};

export type EditorLocalReferenceOption = {
  description?: string;
  imageUrl?: string;
  key: string;
  label: string;
};

/** Check the same source pixels that will be exported. Crop to paint bounds, never rescale radii. */
export function hasLocalMaskPaint(strokes: readonly LocalMaskStroke[], width: number, height: number) {
  if (typeof document === "undefined") return false;
  const canvas = rasterizeMask(strokes, width, height, true);
  if (!canvas) return false;
  const context = canvas.getContext("2d");
  if (!context) return false;
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  for (let index = 3; index < data.length; index += 4) {
    if (data[index] > 0) return true;
  }
  return false;
}

export async function exportLocalMaskBlob(
  strokes: readonly LocalMaskStroke[], width: number, height: number,
): Promise<Blob> {
  const canvas = rasterizeMask(strokes, width, height, false);
  if (!canvas) throw new Error("Invalid mask dimensions or strokes");
  return canvasToPngBlob(canvas);
}

function validMask(strokes: readonly LocalMaskStroke[], width: number, height: number) {
  return Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0 &&
    width <= 16384 && height <= 16384 && width * height <= 67108864 &&
    strokes.every(stroke => ["brush", "erase"].includes(stroke.mode) &&
      Number.isFinite(stroke.radius) && stroke.radius > 0 && stroke.radius <= 16384 &&
      stroke.points.every(point => Number.isFinite(point.x) && Number.isFinite(point.y) &&
        point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1));
}

function rasterizeMask(strokes: readonly LocalMaskStroke[], width: number, height: number, crop: boolean) {
  if (!validMask(strokes, width, height)) return null;
  let left = 0, top = 0, right = width, bottom = height;
  if (crop) {
    left = width; top = height; right = 0; bottom = 0;
    for (const stroke of strokes) {
      if (stroke.mode !== "brush") continue;
      for (const point of stroke.points) {
        // One pixel outside the mathematical bounds retains antialias coverage.
        left = Math.min(left, Math.floor(point.x * width - stroke.radius - 1));
        top = Math.min(top, Math.floor(point.y * height - stroke.radius - 1));
        right = Math.max(right, Math.ceil(point.x * width + stroke.radius + 1));
        bottom = Math.max(bottom, Math.ceil(point.y * height + stroke.radius + 1));
      }
    }
    left = Math.max(0, left); top = Math.max(0, top);
    right = Math.min(width, right); bottom = Math.min(height, bottom);
    if (right <= left || bottom <= top) return null;
  }
  const canvas = document.createElement("canvas");
  canvas.width = right - left; canvas.height = bottom - top;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.lineCap = "round"; context.lineJoin = "round";
  for (const stroke of strokes) {
    if (stroke.points.length === 0) continue;
    context.globalCompositeOperation = stroke.mode === "erase" ? "destination-out" : "source-over";
    context.strokeStyle = "rgba(255,255,255,1)"; context.fillStyle = "rgba(255,255,255,1)";
    context.lineWidth = stroke.radius * 2;
    const firstPoint = stroke.points[0];
    context.beginPath();
    if (stroke.points.length === 1) {
      context.arc(firstPoint.x * width - left, firstPoint.y * height - top, stroke.radius, 0, Math.PI * 2);
      context.fill();
    } else {
      context.moveTo(firstPoint.x * width - left, firstPoint.y * height - top);
      for (const point of stroke.points.slice(1)) context.lineTo(point.x * width - left, point.y * height - top);
      context.stroke();
    }
  }
  context.globalCompositeOperation = "source-over";
  return canvas;
}

function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
        return;
      }
      reject(new Error("Unable to export local mask"));
    }, "image/png");
  });
}
