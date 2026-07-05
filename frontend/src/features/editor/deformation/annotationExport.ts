import type { AnnotationMark } from "./recipe";

const defaultAnnotationColor = "#ef4444";

function clamp01(value: number) {
  return Math.min(1, Math.max(0, Number.isFinite(value) ? value : 0));
}

function annotationColor(annotation: AnnotationMark) {
  return annotation.color || defaultAnnotationColor;
}

function annotationLineWidth(annotation: AnnotationMark, canvasWidth: number, canvasHeight: number) {
  return Math.max(3, annotation.strokeWidth ?? Math.round(Math.min(canvasWidth, canvasHeight) * 0.006));
}

function pointFor(annotation: AnnotationMark, canvasWidth: number, canvasHeight: number) {
  return {
    x: clamp01(annotation.x) * canvasWidth,
    y: clamp01(annotation.y) * canvasHeight,
  };
}

function endPointFor(annotation: AnnotationMark, canvasWidth: number, canvasHeight: number) {
  return {
    x: clamp01(annotation.endX) * canvasWidth,
    y: clamp01(annotation.endY) * canvasHeight,
  };
}

function rectFor(annotation: AnnotationMark, canvasWidth: number, canvasHeight: number) {
  const start = pointFor(annotation, canvasWidth, canvasHeight);
  const end = endPointFor(annotation, canvasWidth, canvasHeight);
  const width = Math.max(1, Math.abs(end.x - start.x) || clamp01(annotation.width) * canvasWidth);
  const height = Math.max(1, Math.abs(end.y - start.y) || clamp01(annotation.height) * canvasHeight);
  return {
    height,
    width,
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
  };
}

function drawNumberBadge(
  context: CanvasRenderingContext2D,
  label: string,
  x: number,
  y: number,
  radius: number,
  color: string,
) {
  context.save();
  context.beginPath();
  context.fillStyle = color;
  context.arc(x, y, radius, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = "#fff";
  context.font = `700 ${Math.max(12, Math.round(radius * 1.15))}px sans-serif`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(label, x, y);
  context.restore();
}

function drawArrowHead(
  context: CanvasRenderingContext2D,
  fromX: number,
  fromY: number,
  toX: number,
  toY: number,
  size: number,
) {
  const angle = Math.atan2(toY - fromY, toX - fromX);
  context.save();
  context.translate(toX, toY);
  context.rotate(angle);
  context.beginPath();
  context.moveTo(0, 0);
  context.lineTo(-size, size * 0.45);
  context.lineTo(-size, -size * 0.45);
  context.lineTo(0, 0);
  context.fill();
  context.restore();
}

export function drawAnnotationsToCanvas(
  context: CanvasRenderingContext2D,
  annotations: readonly AnnotationMark[],
  canvasWidth: number,
  canvasHeight: number,
) {
  annotations.forEach((annotation, index) => {
    const color = annotationColor(annotation);
    const lineWidth = annotationLineWidth(annotation, canvasWidth, canvasHeight);
    const label = String(index + 1);
    const start = pointFor(annotation, canvasWidth, canvasHeight);
    const radius = Math.max(11, Math.round(Math.min(canvasWidth, canvasHeight) * 0.02));

    context.save();
    context.fillStyle = color;
    context.strokeStyle = color;
    context.lineCap = "round";
    context.lineJoin = "round";
    context.lineWidth = lineWidth;

    if (annotation.kind === "arrow") {
      const end = endPointFor(annotation, canvasWidth, canvasHeight);
      context.beginPath();
      context.moveTo(start.x, start.y);
      context.lineTo(end.x, end.y);
      context.stroke();
      drawArrowHead(context, start.x, start.y, end.x, end.y, Math.max(14, lineWidth * 4));
      context.restore();
      return;
    }

    if (annotation.kind === "rect" || annotation.kind === "box") {
      const rect = rectFor(annotation, canvasWidth, canvasHeight);
      context.strokeRect(rect.x, rect.y, rect.width, rect.height);
      context.restore();
      return;
    }

    if (annotation.kind === "text") {
      const text = annotation.text?.trim() || annotation.note.trim() || label;
      context.font = `700 ${annotation.fontSize ?? Math.max(18, Math.round(Math.min(canvasWidth, canvasHeight) * 0.03))}px sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(text, start.x, start.y);
      context.restore();
      return;
    }

    drawNumberBadge(context, label, start.x, start.y, radius, color);
    context.restore();
  });
}
