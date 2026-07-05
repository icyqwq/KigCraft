import { describe, expect, it, vi } from "vitest";
import { drawAnnotationsToCanvas } from "./annotationExport";
import type { AnnotationMark } from "./recipe";

function createMockContext() {
  return {
    beginPath: vi.fn(),
    arc: vi.fn(),
    fill: vi.fn(),
    fillText: vi.fn(),
    lineTo: vi.fn(),
    moveTo: vi.fn(),
    restore: vi.fn(),
    rotate: vi.fn(),
    save: vi.fn(),
    setLineDash: vi.fn(),
    stroke: vi.fn(),
    strokeRect: vi.fn(),
    translate: vi.fn(),
    fillStyle: "",
    font: "",
    lineCap: "",
    lineJoin: "",
    lineWidth: 0,
    strokeStyle: "",
    textAlign: "",
    textBaseline: "",
  };
}

describe("annotation export", () => {
  it("draws pins, arrows, rectangles, and text onto the exported image canvas", () => {
    const context = createMockContext();
    const annotations: AnnotationMark[] = [
      {
        height: 0.08,
        id: "annotation-1",
        kind: "pin",
        note: "keep eye",
        width: 0.08,
        x: 0.25,
        y: 0.3,
        endX: 0.25,
        endY: 0.3,
      },
      {
        height: 0.08,
        id: "annotation-2",
        kind: "arrow",
        note: "move bang",
        width: 0.08,
        x: 0.4,
        y: 0.4,
        endX: 0.7,
        endY: 0.55,
      },
      {
        height: 0.2,
        id: "annotation-3",
        kind: "rect",
        note: "preserve highlight",
        width: 0.24,
        x: 0.1,
        y: 0.2,
        endX: 0.34,
        endY: 0.4,
      },
      {
        height: 0.08,
        id: "annotation-4",
        kind: "text",
        note: "",
        text: "softer mouth",
        width: 0.08,
        x: 0.5,
        y: 0.7,
        endX: 0.5,
        endY: 0.7,
      },
    ];

    drawAnnotationsToCanvas(context as unknown as CanvasRenderingContext2D, annotations, 800, 1000);

    expect(context.arc).toHaveBeenCalledWith(200, 300, expect.any(Number), 0, Math.PI * 2);
    expect(context.moveTo).toHaveBeenCalledWith(320, 400);
    expect(context.lineTo).toHaveBeenCalledWith(560, 550);
    expect(context.strokeRect).toHaveBeenCalledWith(80, 200, 192, 200);
    expect(context.fillText).toHaveBeenCalledWith("softer mouth", 400, 700);
    expect(context.fillText).toHaveBeenCalledWith("1", expect.any(Number), expect.any(Number));
  });
});
