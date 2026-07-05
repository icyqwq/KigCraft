import type { EditRecipe } from "./deformation/recipe";

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

export function hasLocalMaskPaint(strokes: readonly LocalMaskStroke[]) {
  if (typeof document === "undefined") {
    return strokes.some((stroke) => stroke.mode === "brush" && stroke.points.length > 0);
  }

  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  renderLocalMaskStrokes(canvas, strokes);
  const context = canvas.getContext("2d");
  if (!context) return false;
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  for (let index = 3; index < data.length; index += 4) {
    if (data[index] > 0) return true;
  }
  return false;
}

export async function exportLocalMaskBlob(
  strokes: readonly LocalMaskStroke[],
  width: number,
  height: number,
): Promise<Blob> {
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width));
  canvas.height = Math.max(1, Math.round(height));
  renderLocalMaskStrokes(canvas, strokes);
  return canvasToPngBlob(canvas);
}

function renderLocalMaskStrokes(canvas: HTMLCanvasElement, strokes: readonly LocalMaskStroke[]) {
  const context = canvas.getContext("2d");
  if (!context) return;

  context.clearRect(0, 0, canvas.width, canvas.height);
  context.lineCap = "round";
  context.lineJoin = "round";

  for (const stroke of strokes) {
    if (stroke.points.length === 0) continue;
    context.globalCompositeOperation = stroke.mode === "erase" ? "destination-out" : "source-over";
    context.strokeStyle = "rgba(255,255,255,1)";
    context.fillStyle = "rgba(255,255,255,1)";
    context.lineWidth = Math.max(1, stroke.radius * 2);

    const firstPoint = toCanvasPoint(stroke.points[0], canvas.width, canvas.height);
    context.beginPath();
    context.arc(firstPoint.x, firstPoint.y, stroke.radius, 0, Math.PI * 2);
    context.fill();

    if (stroke.points.length > 1) {
      context.beginPath();
      context.moveTo(firstPoint.x, firstPoint.y);
      for (const point of stroke.points.slice(1)) {
        const canvasPoint = toCanvasPoint(point, canvas.width, canvas.height);
        context.lineTo(canvasPoint.x, canvasPoint.y);
      }
      context.stroke();
    }
  }

  context.globalCompositeOperation = "source-over";
}

function toCanvasPoint(point: LocalMaskPoint, width: number, height: number) {
  return {
    x: Math.min(width, Math.max(0, point.x * width)),
    y: Math.min(height, Math.max(0, point.y * height)),
  };
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
