import { afterEach, describe, expect, it, vi } from "vitest";
import { createDefaultLandmarks } from "./landmarks";
import {
  createEmptyRecipe,
  createLiquifyWarpStrokeFromDrag,
  updateEyeControl,
  updateFaceControl,
  updateLiquifyBrush,
  updateLiquifyScaleBrush,
  updateMouthControl,
} from "./recipe";
import {
  calculateDisplacementPixel,
  calculateRecipePreview,
  createEyeMeshTransforms,
  createFeatureLiquifyStrokes,
  createMouthMeshTransforms,
  getDisplacementTextureRadius,
  mountPixiStage,
} from "./pixiStage";

const pixiMocks = vi.hoisted(() => {
  const assetsLoad = vi.fn().mockResolvedValue({ height: 100, width: 100 });
  const textureFrom = vi.fn((source: { height?: number; naturalHeight?: number; naturalWidth?: number; width?: number }) => ({
    height: source.naturalHeight ?? source.height ?? 100,
    source,
    width: source.naturalWidth ?? source.width ?? 100,
  }));
  const applications: Array<{
    canvas: HTMLCanvasElement;
    render: ReturnType<typeof vi.fn>;
  }> = [];
  const containers: Array<{
    position: { set: ReturnType<typeof vi.fn> };
    scale: { set: ReturnType<typeof vi.fn> };
    skew: { set: ReturnType<typeof vi.fn> };
    addChild: ReturnType<typeof vi.fn>;
    removeChildren: ReturnType<typeof vi.fn>;
  }> = [];
  const meshes: Array<{
    autoResize: boolean;
    geometry: { positions: Float32Array };
    scale: { x: number; y: number; set: ReturnType<typeof vi.fn> };
    texture: { height: number; url?: string; width: number };
    x: number;
    y: number;
  }> = [];

  return {
    assetsLoad,
    applications,
    containers,
    meshes,
    reset() {
      assetsLoad.mockReset();
      assetsLoad.mockResolvedValue({ height: 100, width: 100 });
      textureFrom.mockReset();
      textureFrom.mockImplementation(
        (source: { height?: number; naturalHeight?: number; naturalWidth?: number; width?: number }) => ({
          height: source.naturalHeight ?? source.height ?? 100,
          source,
          width: source.naturalWidth ?? source.width ?? 100,
        }),
      );
      applications.length = 0;
      containers.length = 0;
      meshes.length = 0;
    },
    textureFrom,
  };
});

vi.mock("pixi.js", () => {
  class Application {
    canvas = document.createElement("canvas");
    render = vi.fn();
    renderer = { height: 200, width: 300 };
    stage = { addChild: vi.fn() };

    constructor() {
      pixiMocks.applications.push(this);
    }

    async init() {}

    destroy() {}
  }

  class Container {
    position = { set: vi.fn() };
    scale = { set: vi.fn() };
    skew = { set: vi.fn() };
    addChild = vi.fn();
    removeChildren = vi.fn();

    constructor() {
      pixiMocks.containers.push(this);
    }
  }

  class MeshPlane {
    autoResize = true;
    geometry: { positions: Float32Array };
    scale = {
      x: 1,
      y: 1,
      set: vi.fn((x: number, y: number) => {
        this.scale.x = x;
        this.scale.y = y;
      }),
    };
    texture: { height: number; url?: string; width: number };
    x = 0;
    y = 0;

    constructor({
      texture,
      verticesX = 10,
      verticesY = 10,
    }: {
      texture: { height?: number; url?: string; width?: number };
      verticesX?: number;
      verticesY?: number;
    }) {
      this.texture = { height: texture.height ?? 1, url: texture.url, width: texture.width ?? 1 };
      const positions: number[] = [];
      const xSegments = verticesX - 1;
      const ySegments = verticesY - 1;
      for (let y = 0; y < verticesY; y += 1) {
        for (let x = 0; x < verticesX; x += 1) {
          positions.push((x / xSegments) * this.texture.width, (y / ySegments) * this.texture.height);
        }
      }
      this.geometry = { positions: new Float32Array(positions) };
      pixiMocks.meshes.push(this);
    }
  }

  return {
    Application,
    Assets: { load: pixiMocks.assetsLoad },
    Container,
    MeshPlane,
    Texture: { from: pixiMocks.textureFrom },
  };
});

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}

function clonePositions(positions: Float32Array) {
  return Array.from(positions);
}

function positionsChanged(before: number[], after: Float32Array) {
  return after.some((value, index) => value !== before[index]);
}

describe("calculateRecipePreview", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    pixiMocks.reset();
    vi.unstubAllGlobals();
  });

  it("derives local face deformation values from face controls", () => {
    const recipe = updateFaceControl(updateFaceControl(createEmptyRecipe(), "faceWidth", -6), "vLine", 4.8);

    const preview = calculateRecipePreview(recipe);

    expect(preview.imageScale).toEqual({ x: 1, y: 1 });
    expect(preview.jawScale).toEqual({ x: 1, y: 1 });
    expect(preview.displacementScale.x).toBeGreaterThan(0);
    expect(preview.displacementScale.x).toBeLessThanOrEqual(96);
    expect(preview.featureStrokeCount).toBeGreaterThan(0);
  });

  it("gives high face width edits a visibly strong local displacement", () => {
    const recipe = updateFaceControl(createEmptyRecipe(), "faceWidth", 10);

    const preview = calculateRecipePreview(recipe);

    expect(preview.displacementScale.x).toBeGreaterThanOrEqual(60);
  });

  it("converts face length edits into balanced jaw and chin strokes without squaring the jaw", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateFaceControl(createEmptyRecipe(), "faceLength", 10);

    const preview = calculateRecipePreview(recipe, { landmarks });

    expect(preview.displacementScale.y).toBeGreaterThan(0);
    const chinStroke = preview.featureLiquifyStrokes.find((stroke) => stroke.x === landmarks.chin.x && stroke.y === landmarks.chin.y);
    const jawStrokes = preview.featureLiquifyStrokes.filter(
      (stroke) =>
        (stroke.x === landmarks.jawLeft.x && stroke.y === landmarks.jawLeft.y) ||
        (stroke.x === landmarks.jawRight.x && stroke.y === landmarks.jawRight.y),
    );
    const verticalJawStrokes = jawStrokes.filter((stroke) => stroke.mode === "push-down");
    expect(verticalJawStrokes).toHaveLength(2);
    expect(chinStroke?.strength).toBeLessThanOrEqual(verticalJawStrokes[0].strength + 0.03);
    expect(preview.featureLiquifyStrokes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ x: landmarks.chin.x, mode: "push-up" }),
        expect.objectContaining({ x: landmarks.jawLeft.x, y: landmarks.jawLeft.y, mode: "push-down" }),
        expect.objectContaining({ x: landmarks.jawRight.x, y: landmarks.jawRight.y, mode: "push-down" }),
        expect.objectContaining({ x: landmarks.jawLeft.x, y: landmarks.jawLeft.y, mode: "push-right" }),
        expect.objectContaining({ x: landmarks.jawRight.x, y: landmarks.jawRight.y, mode: "push-left" }),
        expect.objectContaining({ x: landmarks.chin.x, y: landmarks.chin.y, mode: "push-down" }),
      ]),
    );
  });

  it("converts mid-face length edits into local mouth and cheek vertical strokes", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateFaceControl(createEmptyRecipe(), "midFaceLength", 0.4);

    const preview = calculateRecipePreview(recipe, { landmarks });

    expect(preview.displacementScale.y).toBeGreaterThan(0);
    expect(preview.featureLiquifyStrokes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          mode: "push-down",
          x: landmarks.mouthCenter.x,
          y: landmarks.mouthCenter.y,
        }),
      ]),
    );
    expect(preview.featureLiquifyStrokes.every((stroke) => stroke.y !== landmarks.chin.y)).toBe(true);
  });

  it("converts face controls into local liquify strokes around jaw and chin landmarks", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateFaceControl(updateFaceControl(createEmptyRecipe(), "vLine", 6.4), "smallFace", 3.6);

    const preview = calculateRecipePreview(recipe, { landmarks });

    expect(preview.imageScale).toEqual({ x: 1, y: 1 });
    expect(preview.imageOffset).toEqual({ x: 0, y: 0 });
    expect(preview.featureStrokeCount).toBeGreaterThanOrEqual(3);
    expect(preview.featureLiquifyStrokes).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ x: landmarks.jawLeft.x, y: landmarks.jawLeft.y, mode: "push-right" }),
        expect.objectContaining({ x: landmarks.jawRight.x, y: landmarks.jawRight.y, mode: "push-left" }),
        expect.objectContaining({ x: landmarks.chin.x, y: landmarks.chin.y }),
      ]),
    );
  });

  it("derives local eye deformation values from eye controls", () => {
    const recipe = updateEyeControl(updateEyeControl(createEmptyRecipe(), "eyeSize", 1.8), "eyeTilt", -2);

    const preview = calculateRecipePreview(recipe);

    expect(preview.eyeScale).toEqual({ x: 1, y: 1 });
    expect(preview.eyeSkew).toBe(0);
    expect(preview.eyeMeshTransforms).toHaveLength(2);
    expect(preview.eyeTransformCount).toBe(2);
  });

  it("converts eye controls into local mesh transforms around detected eye landmarks", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(updateEyeControl(createEmptyRecipe(), "eyeHeight", 2.2), "eyeDistance", 1.6);

    const preview = calculateRecipePreview(recipe, { landmarks });

    expect(preview.eyeScale).toEqual({ x: 1, y: 1 });
    expect(preview.eyeOffset).toEqual({ x: 0, y: 0 });
    expect(preview.featureLiquifyStrokes).toEqual([]);
    expect(preview.eyeMeshTransforms).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ centerX: landmarks.leftEye.x, centerY: landmarks.leftEye.y }),
        expect.objectContaining({ centerX: landmarks.rightEye.x, centerY: landmarks.rightEye.y }),
      ]),
    );
  });

  it("moves eye distance by translating each eye region without scaling the eye shape", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeDistance", 6);

    const transforms = createEyeMeshTransforms(recipe, landmarks);

    expect(transforms).toEqual([
      expect.objectContaining({ centerX: landmarks.leftEye.x, scaleX: 1, scaleY: 1, translateY: 0 }),
      expect.objectContaining({ centerX: landmarks.rightEye.x, scaleX: 1, scaleY: 1, translateY: 0 }),
    ]);
    expect(transforms[0].translateX).toBeLessThan(0);
    expect(transforms[1].translateX).toBeGreaterThan(0);
    expect(transforms[0].rotation).toBe(0);
    expect(transforms[1].rotation).toBe(0);
  });

  it("treats the clamped eye distance and vertical recipe values as the full real range", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(
      updateEyeControl(createEmptyRecipe(), "eyeDistance", 0.05),
      "eyeVertical",
      -0.05,
    );

    const transforms = createEyeMeshTransforms(recipe, landmarks);

    expect(transforms[0].translateX).toBe(-34);
    expect(transforms[1].translateX).toBe(34);
    expect(transforms[0].translateY).toBe(-28);
    expect(transforms[1].translateY).toBe(-28);
  });

  it("uses eye size for uniform scaling and eye height for vertical-only scaling", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(updateEyeControl(createEmptyRecipe(), "eyeSize", 3), "eyeHeight", 4);

    const transforms = createEyeMeshTransforms(recipe, landmarks);
    const leftTransform = transforms.find((transform) => transform.centerX === landmarks.leftEye.x);

    expect(leftTransform?.scaleX).toBeGreaterThan(1);
    expect(leftTransform?.scaleY).toBeGreaterThan(leftTransform?.scaleX ?? 0);
  });

  it("rotates eye tilt as mirrored left and right eye angles", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeTilt", 5);

    const transforms = createEyeMeshTransforms(recipe, landmarks);

    expect(transforms).toHaveLength(2);
    expect(transforms[0].rotation).toBeLessThan(0);
    expect(transforms[1].rotation).toBeGreaterThan(0);
    expect(Math.abs(transforms[0].rotation)).toBeCloseTo(Math.abs(transforms[1].rotation), 4);
  });

  it("converts mouth controls into one local mesh transform around mouth landmarks", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateMouthControl(
      updateMouthControl(
        updateMouthControl(updateMouthControl(createEmptyRecipe(), "mouthWidth", 0.45), "mouthVertical", -0.06),
        "mouthSmile",
        0.08,
      ),
      "mouthSize",
      0.35,
    );

    const preview = calculateRecipePreview(recipe, { landmarks });
    const transforms = createMouthMeshTransforms(recipe, landmarks);

    expect(preview.mouthTransformCount).toBe(1);
    expect(transforms).toEqual([
      expect.objectContaining({
        centerX: landmarks.mouthCenter.x,
        centerY: landmarks.mouthCenter.y,
        smile: 1,
        translateY: -24,
      }),
    ]);
    expect(transforms[0].scaleX).toBeGreaterThan(transforms[0].scaleY);
  });

  it("derives displacement values from liquify strokes", () => {
    const recipe = updateLiquifyBrush(createEmptyRecipe(), {
      x: 0.5,
      y: 0.5,
      radius: 120,
      strength: 0.75,
      mode: "push-right",
    });

    const preview = calculateRecipePreview(recipe);

    expect(preview.displacementScale.x).toBeGreaterThan(0);
    expect(preview.imageOffset).toEqual({ x: 0, y: 0 });
    expect(preview.liquifyIntensity).toBeGreaterThan(0);
    expect(preview.strokeCount).toBe(1);
  });

  it("uses drag distance and direction for deformation brush strokes", () => {
    const stroke = createLiquifyWarpStrokeFromDrag({
      from: { x: 0.4, y: 0.5 },
      radius: 96,
      to: { x: 0.52, y: 0.46 },
    });

    const centerPixel = calculateDisplacementPixel([stroke], { x: stroke.x, y: stroke.y });
    const edgePixel = calculateDisplacementPixel([stroke], { x: stroke.x + 0.35, y: stroke.y });

    expect(centerPixel.red).toBeGreaterThan(128);
    expect(centerPixel.green).toBeLessThan(128);
    expect(Math.abs(edgePixel.red - 128)).toBeLessThan(Math.abs(centerPixel.red - 128));
    expect(Math.abs(edgePixel.green - 128)).toBeLessThan(Math.abs(centerPixel.green - 128));
  });

  it("uses a soft radial falloff for local scale strokes", () => {
    const recipe = updateLiquifyScaleBrush(createEmptyRecipe(), {
      radius: 96,
      scale: 0.6,
      x: 0.5,
      y: 0.5,
    });
    const stroke = recipe.liquify[0];

    const innerPixel = calculateDisplacementPixel([stroke], { x: 0.58, y: 0.5 });
    const outerPixel = calculateDisplacementPixel([stroke], { x: 0.84, y: 0.5 });

    expect(innerPixel.red).toBeGreaterThan(128);
    expect(Math.abs(outerPixel.red - 128)).toBeLessThan(Math.abs(innerPixel.red - 128));
  });

  it("converts screen-space brush radius to displacement-map radius", () => {
    expect(getDisplacementTextureRadius(72)).toBeLessThan(32);
    expect(getDisplacementTextureRadius(160)).toBeLessThan(64);
  });

  it("keeps a long manual liquify drag from compounding displacement scale", () => {
    let recipe = createEmptyRecipe();
    for (let index = 0; index < 16; index += 1) {
      recipe = updateLiquifyBrush(recipe, {
        x: 0.28 + index * 0.028,
        y: 0.47,
        radius: 72,
        strength: 0.48,
        mode: "push-right",
      });
    }

    const preview = calculateRecipePreview(recipe);

    expect(preview.strokeCount).toBe(16);
    expect(preview.displacementScale.x).toBeLessThanOrEqual(96);
  });

  it("keeps high face controls within a local deformation scale", () => {
    const recipe = updateFaceControl(updateFaceControl(createEmptyRecipe(), "smallFace", 9.8), "vLine", 9.8);

    const preview = calculateRecipePreview(recipe);

    expect(preview.featureStrokeCount).toBeGreaterThan(0);
    expect(preview.displacementScale.x).toBeLessThanOrEqual(96);
    expect(preview.displacementScale.y).toBeLessThanOrEqual(96);
  });

  it("combines overlapping face slider strokes instead of letting the last stroke overwrite the previous one", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateFaceControl(updateFaceControl(createEmptyRecipe(), "faceWidth", 10), "smallFace", 6.6);
    const strokes = createFeatureLiquifyStrokes(recipe, landmarks);

    const jawLeftPixel = calculateDisplacementPixel(strokes, landmarks.jawLeft);

    expect(jawLeftPixel.red).toBeLessThan(128);
  });

  it("resets the deformation mesh for an empty recipe and moves vertices for face controls", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);
    await stage.setImageUrl("/candidate.webp");
    const imageMesh = pixiMocks.meshes[0];
    const basePositions = clonePositions(imageMesh.geometry.positions);

    stage.applyRecipe(createEmptyRecipe());

    expect(clonePositions(imageMesh.geometry.positions)).toEqual(basePositions);

    stage.applyRecipe(updateFaceControl(createEmptyRecipe(), "faceWidth", 4));

    expect(positionsChanged(basePositions, imageMesh.geometry.positions)).toBe(true);

    stage.destroy();
  });

  it("renders a fresh frame after recipe changes update the deformation mesh", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);
    await stage.setImageUrl("/candidate.webp");
    pixiMocks.applications[0].render.mockClear();
    const imageMesh = pixiMocks.meshes[0];
    const basePositions = clonePositions(imageMesh.geometry.positions);

    stage.applyRecipe(updateFaceControl(createEmptyRecipe(), "faceWidth", 10));

    expect(positionsChanged(basePositions, imageMesh.geometry.positions)).toBe(true);
    expect(pixiMocks.applications[0].render).toHaveBeenCalledTimes(1);

    stage.destroy();
  });

  it("fits the deformation mesh to the displayed image area", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);

    await stage.setImageUrl("/candidate.webp");

    const imageMesh = pixiMocks.meshes[0];
    expect(imageMesh.scale.set).toHaveBeenLastCalledWith(2, 2);
    expect(imageMesh.x).toBe(50);
    expect(imageMesh.y).toBe(0);

    stage.destroy();
  });

  it("loads blob image URLs through the browser image decoder instead of Pixi Assets", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    class ImageStub {
      crossOrigin = "";
      height = 100;
      naturalHeight = 100;
      naturalWidth = 100;
      onerror: (() => void) | null = null;
      onload: (() => void) | null = null;
      width = 100;

      set src(_value: string) {
        queueMicrotask(() => this.onload?.());
      }
    }
    vi.stubGlobal("Image", ImageStub);
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);

    await stage.setImageUrl("blob:http://127.0.0.1:15175/local-image");

    expect(pixiMocks.assetsLoad).not.toHaveBeenCalled();
    expect(pixiMocks.textureFrom).toHaveBeenCalledWith(expect.any(ImageStub));
    expect(pixiMocks.meshes).toHaveLength(1);

    stage.destroy();
  });

  it("keeps eye controls local instead of scaling or moving the whole preview container", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);
    await stage.setImageUrl("/candidate.webp");
    const imageContainer = pixiMocks.containers[0];
    const imageMesh = pixiMocks.meshes[0];
    const basePositions = clonePositions(imageMesh.geometry.positions);

    const recipe = updateEyeControl(
      updateEyeControl(
        updateEyeControl(updateEyeControl(createEmptyRecipe(), "eyeSize", 6.4), "eyeWidth", 2),
        "eyeDistance",
        1,
      ),
      "eyeVertical",
      -0.8,
    );
    stage.applyRecipe(recipe);

    expect(imageContainer.scale.set).toHaveBeenLastCalledWith(1, 1);
    expect(imageContainer.position.set).toHaveBeenLastCalledWith(0, 0);
    expect(positionsChanged(basePositions, imageMesh.geometry.positions)).toBe(true);

    stage.destroy();
  });

  it("keeps a slower previous image load from replacing a newer image", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const loads = new Map<
      string,
      {
        promise: Promise<{ height: number; url: string; width: number }>;
        resolve: (texture: { height: number; url: string; width: number }) => void;
      }
    >();
    pixiMocks.assetsLoad.mockImplementation((url: string) => {
      let resolveLoad: (texture: { height: number; url: string; width: number }) => void = () => undefined;
      const promise = new Promise<{ height: number; url: string; width: number }>((resolve) => {
        resolveLoad = resolve;
      });
      loads.set(url, { promise, resolve: resolveLoad });
      return promise;
    });
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);

    const firstLoad = stage.setImageUrl("/candidate-a.webp");
    const secondLoad = stage.setImageUrl("/candidate-b.webp");
    loads.get("/candidate-b.webp")?.resolve({ height: 100, url: "/candidate-b.webp", width: 100 });
    await secondLoad;
    loads.get("/candidate-a.webp")?.resolve({ height: 100, url: "/candidate-a.webp", width: 100 });
    await firstLoad;

    const imageContainer = pixiMocks.containers[0];
    const loadedImageMeshes = pixiMocks.meshes.filter((mesh) => mesh.texture.url);
    expect(imageContainer.removeChildren).toHaveBeenCalledTimes(1);
    expect(loadedImageMeshes).toHaveLength(1);
    expect(loadedImageMeshes[0].texture.url).toBe("/candidate-b.webp");

    stage.destroy();
  });
});

