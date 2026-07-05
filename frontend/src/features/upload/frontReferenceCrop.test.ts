import { afterEach, describe, expect, it, vi } from "vitest";
import type { LocalMaskStroke } from "../editor/localGeneration";
import {
  createFaceBoxMaskStrokes,
  cropImageElementWithMask,
  getMaskBounds,
  isReliableFaceBoxDetection,
  maskBoundsToCropRect,
} from "./frontReferenceCrop";

describe("frontReferenceCrop", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("creates a single continuous initial mask expanded around the detected head box", () => {
    const strokes = createFaceBoxMaskStrokes(
      { height: 240, width: 220, x: 100, y: 80 },
      500,
      400,
    );

    expect(strokes).toHaveLength(1);
    expect(strokes[0]).toEqual(expect.objectContaining({ mode: "brush" }));
    expect(strokes[0].points).toHaveLength(2);
    expect(strokes[0].points[0].x).toBeCloseTo(0.42);
    expect(strokes[0].points[0].y).toBeLessThan(0.475);
    expect(strokes[0].points[1].x).toBeCloseTo(0.42);
    expect(strokes[0].points[1].y).toBeGreaterThan(0.525);
    expect(strokes[0].radius).toBeGreaterThan(110);
  });

  it("computes padded crop bounds from painted mask pixels", () => {
    const data = new Uint8ClampedArray(10 * 10 * 4);
    for (let y = 3; y <= 6; y += 1) {
      for (let x = 2; x <= 5; x += 1) {
        data[(y * 10 + x) * 4 + 3] = 255;
      }
    }

    const bounds = getMaskBounds(data, 10, 10);
    const crop = maskBoundsToCropRect(bounds, 10, 10, 0.1);

    expect(crop).toEqual({ height: 6, width: 6, x: 1, y: 2 });
  });

  it("rejects an empty mask", () => {
    const data = new Uint8ClampedArray(8 * 8 * 4);

    expect(() => getMaskBounds(data, 8, 8)).toThrow("front_reference_mask_empty");
  });

  it("rejects obvious false positive face boxes away from the character face area", () => {
    expect(
      isReliableFaceBoxDetection(
        { height: 360, width: 360, x: 10, y: 250 },
        940,
        884,
        0.92,
      ),
    ).toBe(false);

    expect(
      isReliableFaceBoxDetection(
        { height: 130, width: 120, x: 560, y: 150 },
        940,
        884,
        0.92,
      ),
    ).toBe(true);
  });

  it("uses the painted mask as output alpha instead of exporting the whole crop rectangle", async () => {
    const originalCreateElement = document.createElement.bind(document);
    const sourceImageDraw = vi.fn();
    const maskAlphaDraw = vi.fn();
    const outputOperations: string[] = [];
    let outputCompositeOperation = "source-over";

    const maskContext = {
      arc: vi.fn(),
      beginPath: vi.fn(),
      clearRect: vi.fn(),
      fill: vi.fn(),
      getImageData: () => {
        const data = new Uint8ClampedArray(6 * 6 * 4);
        for (let y = 2; y <= 3; y += 1) {
          for (let x = 2; x <= 3; x += 1) {
            data[(y * 6 + x) * 4 + 3] = 255;
          }
        }
        return { data };
      },
      lineTo: vi.fn(),
      moveTo: vi.fn(),
      stroke: vi.fn(),
    };
    const maskCanvas = {
      getContext: () => maskContext,
      height: 0,
      width: 0,
    } as unknown as HTMLCanvasElement;
    const outputContext = {
      drawImage: vi.fn((source: CanvasImageSource, ...args: unknown[]) => {
        if (source === maskCanvas) {
          maskAlphaDraw(...args);
          return;
        }
        sourceImageDraw(source, ...args);
      }),
      get globalCompositeOperation() {
        return outputCompositeOperation;
      },
      set globalCompositeOperation(value: string) {
        outputOperations.push(value);
        outputCompositeOperation = value;
      },
    };
    const outputCanvas = {
      getContext: () => outputContext,
      height: 0,
      toBlob: (callback: BlobCallback) => callback(new Blob(["png"], { type: "image/png" })),
      width: 0,
    } as unknown as HTMLCanvasElement;
    const canvases = [maskCanvas, outputCanvas];
    vi.spyOn(document, "createElement").mockImplementation(((tagName: string) => {
      if (tagName !== "canvas") return originalCreateElement(tagName);
      const canvas = canvases.shift();
      if (!canvas) throw new Error("unexpected_canvas");
      return canvas;
    }) as typeof document.createElement);

    const image = { height: 6, naturalHeight: 6, naturalWidth: 6, width: 6 } as HTMLImageElement;
    const strokes: LocalMaskStroke[] = [{ id: "mask", mode: "brush", points: [{ x: 0.5, y: 0.5 }], radius: 1 }];

    await cropImageElementWithMask(new File(["x"], "front.png", { type: "image/png" }), image, strokes);

    expect(sourceImageDraw).toHaveBeenCalledWith(image, 1, 1, 4, 4, 0, 0, 4, 4);
    expect(outputOperations).toContain("destination-in");
    expect(maskAlphaDraw).toHaveBeenCalledWith(1, 1, 4, 4, 0, 0, 4, 4);
    expect(outputOperations.at(-1)).toBe("source-over");
  });

  it("rejects crop export when canvas cannot produce a blob", async () => {
    const originalCreateElement = document.createElement.bind(document);
    vi.spyOn(document, "createElement").mockImplementation(((tagName: string) => {
      if (tagName !== "canvas") return originalCreateElement(tagName);
      return {
        getContext: () => ({
          clearRect: vi.fn(),
          drawImage: vi.fn(),
          fill: vi.fn(),
          getImageData: () => {
            const data = new Uint8ClampedArray(4 * 4 * 4);
            data[3] = 255;
            return { data };
          },
          lineTo: vi.fn(),
          moveTo: vi.fn(),
          arc: vi.fn(),
          beginPath: vi.fn(),
          stroke: vi.fn(),
        }),
        height: 0,
        toBlob: (callback: BlobCallback) => callback(null),
        width: 0,
      } as unknown as HTMLCanvasElement;
    }) as typeof document.createElement);

    const { cropImageElementWithMask } = await import("./frontReferenceCrop");
    const image = { height: 4, naturalHeight: 4, naturalWidth: 4, width: 4 } as HTMLImageElement;
    const strokes: LocalMaskStroke[] = [{ id: "mask", mode: "brush", points: [{ x: 0.5, y: 0.5 }], radius: 1 }];

    await expect(cropImageElementWithMask(new File(["x"], "front.png", { type: "image/png" }), image, strokes)).rejects.toThrow(
      "front_reference_crop_failed",
    );
  });
});
