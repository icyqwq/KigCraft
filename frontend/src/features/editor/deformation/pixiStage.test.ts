import { anchorRecipeToImage } from "../core/imageCoordinates";
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
  getEyeDisplacement,
  mountPixiStage,
  type EyeMeshTransform,
} from "./pixiStage";

const eyeRadius = { bottom: 60, top: 80, x: 100 };

function eyeOffset(transform: EyeMeshTransform, x: number, y: number) {
  return getEyeDisplacement(transform, x, y, eyeRadius.x, eyeRadius.top, eyeRadius.bottom);
}

/** Smallest Jacobian determinant of the deformed eye area; zero or below means the mesh folds over. */
function minimumEyeJacobian(transform: EyeMeshTransform) {
  const step = 0.5;
  let minimum = Infinity;
  for (let y = -eyeRadius.top * 1.7; y <= eyeRadius.bottom * 1.7; y += 3) {
    for (let x = -eyeRadius.x * 1.4; x <= eyeRadius.x * 1.4; x += 3) {
      const origin = eyeOffset(transform, x, y);
      const right = eyeOffset(transform, x + step, y);
      const down = eyeOffset(transform, x, y + step);
      const dxdx = 1 + (right.x - origin.x) / step;
      const dydx = (right.y - origin.y) / step;
      const dxdy = (down.x - origin.x) / step;
      const dydy = 1 + (down.y - origin.y) / step;
      minimum = Math.min(minimum, dxdx * dydy - dxdy * dydx);
    }
  }
  return minimum;
}

const pixiMocks = vi.hoisted(() => {
  const extractCanvas = vi.fn();
  const init = vi.fn();
  const assetsLoad = vi.fn().mockResolvedValue({ height: 100, width: 100 });
  const textureFrom = vi.fn((source: { height?: number; naturalHeight?: number; naturalWidth?: number; width?: number }) => ({
    height: source.naturalHeight ?? source.height ?? 100,
    source,
    width: source.naturalWidth ?? source.width ?? 100,
  }));
  const applications: Array<{
    canvas: HTMLCanvasElement;
    render: ReturnType<typeof vi.fn>;
    renderer: { width: number; height: number };
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
    extractCanvas,
    init,
    applications,
    containers,
    meshes,
    reset() {
      extractCanvas.mockReset();
      init.mockReset();
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
    renderer = { height: 200, width: 300, extract: { canvas: pixiMocks.extractCanvas } };
    stage = { addChild: vi.fn() };

    constructor() {
      pixiMocks.applications.push(this);
    }

    async init() { await pixiMocks.init(); }

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
    destroy = vi.fn();
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
    Rectangle: class { constructor(public x: number, public y: number, public width: number, public height: number) {} },
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
    expect(preview.faceShapeTransform).not.toBeNull();
    expect(preview.featureStrokeCount).toBe(0);
  });

  it("clamps excessive face width while retaining stronger local displacement than the midpoint", () => {
    const preview = calculateRecipePreview(updateFaceControl(createEmptyRecipe(), "faceWidth", 10));
    const atLimit = calculateRecipePreview(updateFaceControl(createEmptyRecipe(), "faceWidth", 0.4));
    const midpoint = calculateRecipePreview(updateFaceControl(createEmptyRecipe(), "faceWidth", 0.2));

    expect(preview).toEqual(atLimit);
    expect(preview.displacementScale.x).toBeGreaterThan(midpoint.displacementScale.x);
    expect(midpoint.displacementScale.x).toBeGreaterThan(0);
    expect(preview.imageScale).toEqual({x: 1, y: 1});
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

  it("deforms face shape along an outline through the face sides, jaws and chin", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateFaceControl(updateFaceControl(createEmptyRecipe(), "vLine", 6.4), "smallFace", 3.6);

    const preview = calculateRecipePreview(recipe, { landmarks });
    const contour = preview.faceShapeTransform!.contour;

    expect(preview.imageScale).toEqual({ x: 1, y: 1 });
    expect(preview.imageOffset).toEqual({ x: 0, y: 0 });
    expect(contour).toHaveLength(7);
    expect(contour.slice(2, 5)).toEqual([landmarks.jawLeft, landmarks.chin, landmarks.jawRight]);
    expect(contour[1].x).toBeLessThan(landmarks.leftEye.x);
    expect(contour[5].x).toBeGreaterThan(landmarks.rightEye.x);
    expect(contour[0].y).toBeLessThan(landmarks.leftEye.y);
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

  it("deforms eye distance inside each eye without scaling the eye shape", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeDistance", 6);

    const transforms = createEyeMeshTransforms(recipe, landmarks);

    expect(transforms).toEqual([
      expect.objectContaining({ centerX: landmarks.leftEye.x, outwardSign: -1, scaleX: 1, scaleY: 1, shiftY: 0 }),
      expect.objectContaining({ centerX: landmarks.rightEye.x, outwardSign: 1, scaleX: 1, scaleY: 1, shiftY: 0 }),
    ]);
    expect(transforms[0].shiftX).toBeGreaterThan(0);
    expect(transforms[0].rotation).toBe(0);
    expect(transforms[1].rotation).toBe(0);
    expect(eyeOffset(transforms[0], 0, 0).x).toBeLessThan(0);
    expect(eyeOffset(transforms[1], 0, 0).x).toBeGreaterThan(0);
    expect(eyeOffset(transforms[1], eyeRadius.x, 0).x).toBeCloseTo(0, 6);
  });

  it.each([[0.05, 0.075, 0.13], [0.1, 0.15, 0.26], [1, 0.15, 0.26]])("maps eye movement %s proportionally and clamps at the full range", (value, shiftX, shiftY) => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(
      updateEyeControl(createEmptyRecipe(), "eyeDistance", value),
      "eyeVertical",
      -value,
    );

    const transforms = createEyeMeshTransforms(recipe, landmarks);

    expect(transforms.map((transform) => transform.shiftX)).toEqual([shiftX, shiftX]);
    expect(transforms.map((transform) => transform.shiftY)).toEqual([-shiftY, -shiftY]);
  });

  it("deforms the eye up with smooth falloff instead of moving a rigid patch", () => {
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeVertical", -0.05);
    const [left] = createEyeMeshTransforms(recipe, createDefaultLandmarks(1, 1));

    const center = eyeOffset(left, 0, 0);
    const upperLash = eyeOffset(left, 0, -eyeRadius.top * 0.85);
    const innerCorner = eyeOffset(left, eyeRadius.x * 0.55, 0);
    const outerCorner = eyeOffset(left, -eyeRadius.x * 0.55, 0);

    expect(center.x).toBeCloseTo(0, 6);
    expect(center.y).toBeCloseTo(-0.13 * eyeRadius.bottom, 4);
    expect(upperLash.y).toBeLessThan(center.y * 0.5);
    expect(upperLash.y).toBeGreaterThan(center.y);
    expect(Math.abs(outerCorner.y)).toBeLessThan(Math.abs(innerCorner.y) * 0.5);
    expect(eyeOffset(left, 0, -eyeRadius.top * 1.4).y).toBe(0);
    expect(eyeOffset(left, 0, eyeRadius.bottom * 1.25).y).toBe(0);
  });

  it("lengthens only the inside of the eye instead of widening the whole eye region", () => {
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeWidth", 0.5);
    const [, right] = createEyeMeshTransforms(recipe, createDefaultLandmarks(1, 1));

    expect(right).toEqual(expect.objectContaining({ scaleX: 1, scaleY: 1 }));
    expect(eyeOffset(right, eyeRadius.x * 0.5, 0).x).toBeGreaterThan(0);
    expect(eyeOffset(right, -eyeRadius.x * 0.5, 0).x).toBeLessThan(0);
    expect(eyeOffset(right, eyeRadius.x, 0).x).toBeCloseTo(0, 6);
    expect(eyeOffset(right, eyeRadius.x * 1.1, 0).x).toBe(0);
    expect(eyeOffset(right, eyeRadius.x * 0.5, -eyeRadius.top).x).toBeCloseTo(0, 6);
  });

  it("lifts only the outer eye corners and keeps the inner corners anchored", () => {
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeTailLift", 0.5);
    const [left, right] = createEyeMeshTransforms(recipe, createDefaultLandmarks(1, 1));

    expect(eyeOffset(left, -eyeRadius.x * 0.6, 0).y).toBeLessThan(0);
    expect(eyeOffset(right, eyeRadius.x * 0.6, 0).y).toBeLessThan(0);
    expect(eyeOffset(left, eyeRadius.x * 0.6, 0).y).toBe(0);
    expect(eyeOffset(right, -eyeRadius.x * 0.6, 0).y).toBe(0);
    expect(eyeOffset(left, -eyeRadius.x * 0.6, 0).y).toBeCloseTo(eyeOffset(right, eyeRadius.x * 0.6, 0).y, 6);
  });

  it("opens the lids and enlarges the iris without moving the eye center", () => {
    const lidRecipe = updateEyeControl(updateEyeControl(createEmptyRecipe(), "eyeUpperLid", 0.5), "eyeLowerLid", 0.5);
    const [lids] = createEyeMeshTransforms(lidRecipe, createDefaultLandmarks(1, 1));

    expect(eyeOffset(lids, 0, -eyeRadius.top * 0.75).y).toBeLessThan(0);
    expect(eyeOffset(lids, 0, eyeRadius.bottom * 0.75).y).toBeGreaterThan(0);
    expect(eyeOffset(lids, 0, 0)).toEqual({ x: 0, y: 0 });

    const irisRecipe = updateEyeControl(createEmptyRecipe(), "eyeIrisSize", 0.5);
    const [iris] = createEyeMeshTransforms(irisRecipe, createDefaultLandmarks(1, 1));
    const irisRadius = 0.9 * Math.min(eyeRadius.x, (eyeRadius.top + eyeRadius.bottom) / 2);

    expect(eyeOffset(iris, irisRadius * 0.3, 0).x).toBeGreaterThan(0);
    expect(eyeOffset(iris, irisRadius, 0).x).toBeCloseTo(0, 6);
    expect(eyeOffset(iris, 0, 0)).toEqual({ x: 0, y: 0 });
  });

  it.each([
    ["eyeSize", 0.6], ["eyeSize", -0.6], ["eyeHeight", 0.6], ["eyeHeight", -0.6],
    ["eyeWidth", 1], ["eyeWidth", -1], ["eyeDistance", 0.1], ["eyeDistance", -0.1],
    ["eyeVertical", 0.1], ["eyeVertical", -0.1], ["eyeTilt", 1], ["eyeIrisSize", 1], ["eyeIrisSize", -1],
    ["eyeTailLift", 1], ["eyeTailLift", -1], ["eyeUpperLid", 1], ["eyeUpperLid", -1],
    ["eyeLowerLid", 1], ["eyeLowerLid", -1],
  ] as const)("keeps the eye mesh from folding over at %s %s", (key, value) => {
    const recipe = updateEyeControl(createEmptyRecipe(), key, value);
    const [left, right] = createEyeMeshTransforms(recipe, createDefaultLandmarks(1, 1));

    expect(minimumEyeJacobian(left)).toBeGreaterThan(0.05);
    expect(minimumEyeJacobian(right)).toBeGreaterThan(0.05);
  });

  it("uses eye size for uniform scaling and eye height for vertical-only scaling", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(updateEyeControl(createEmptyRecipe(), "eyeSize", 3), "eyeHeight", 4);

    const transforms = createEyeMeshTransforms(recipe, landmarks);
    const leftTransform = transforms.find((transform) => transform.centerX === landmarks.leftEye.x);

    expect(leftTransform?.scaleX).toBeGreaterThan(1);
    expect(leftTransform?.scaleY).toBeGreaterThan(leftTransform?.scaleX ?? 0);
  });

  it("rotates eye tilt as mirrored angles that lift the outer corners", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const recipe = updateEyeControl(createEmptyRecipe(), "eyeTilt", 5);

    const transforms = createEyeMeshTransforms(recipe, landmarks);

    expect(transforms).toHaveLength(2);
    expect(transforms[0].rotation).toBeGreaterThan(0);
    expect(transforms[1].rotation).toBeLessThan(0);
    expect(Math.abs(transforms[0].rotation)).toBeCloseTo(Math.abs(transforms[1].rotation), 4);
    expect(eyeOffset(transforms[0], -eyeRadius.x * 0.6, 0).y).toBeLessThan(0);
    expect(eyeOffset(transforms[1], eyeRadius.x * 0.6, 0).y).toBeLessThan(0);
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
        translateY: -0.2,
      }),
    ]);
    expect(transforms[0].scaleX).toBeGreaterThan(transforms[0].scaleY);
  });

  it("keeps the mouth patch inside the face so mouth edits never move the jaw line or chin", () => {
    // Landmarks as detected on a real head-shell render: the jaw corners sit at mouth height.
    const landmarks = {
      ...createDefaultLandmarks(1, 1),
      leftEye: { x: 0.3934, y: 0.3745 }, rightEye: { x: 0.6201, y: 0.3745 },
      chin: { x: 0.5067, y: 0.5389 }, jawLeft: { x: 0.3533, y: 0.4641 }, jawRight: { x: 0.6427, y: 0.4716 },
      mouthCenter: { x: 0.5041, y: 0.4697 }, mouthLeft: { x: 0.4649, y: 0.4666 }, mouthRight: { x: 0.5451, y: 0.4641 },
    };
    let recipe = updateMouthControl(createEmptyRecipe(), "mouthWidth", 0.45);
    recipe = updateMouthControl(recipe, "mouthSize", 0.35);
    recipe = updateMouthControl(recipe, "mouthHorizontal", 0.05);
    recipe = updateMouthControl(recipe, "mouthVertical", 0.06);
    const [patch] = createMouthMeshTransforms(recipe, landmarks);

    expect(patch.centerX - patch.radiusX).toBeGreaterThan(landmarks.jawLeft.x);
    expect(patch.centerX + patch.radiusX).toBeLessThan(landmarks.jawRight.x);
    expect(patch.centerY + patch.radiusY).toBeLessThan(landmarks.chin.y);
    for (const point of [landmarks.jawLeft, landmarks.jawRight, landmarks.chin]) {
      const localX = point.x - patch.centerX, localY = point.y - patch.centerY;
      const outside = Math.abs(localX) >= patch.radiusX || Math.abs(localY) >= patch.radiusY;
      expect(outside).toBe(true);
    }
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

    expect(preview.faceShapeTransform).not.toBeNull();
    expect(preview.displacementScale.x).toBeLessThanOrEqual(96);
    expect(preview.displacementScale.y).toBeLessThanOrEqual(96);
  });

  it("adds overlapping face sliders instead of letting the last one overwrite the previous one", () => {
    const landmarks = createDefaultLandmarks(1, 1);
    const both = calculateRecipePreview(
      updateFaceControl(updateFaceControl(createEmptyRecipe(), "faceWidth", 0.2), "smallFace", 0.2), { landmarks });
    const width = calculateRecipePreview(updateFaceControl(createEmptyRecipe(), "faceWidth", 0.2), { landmarks });
    const small = calculateRecipePreview(updateFaceControl(createEmptyRecipe(), "smallFace", 0.2), { landmarks });
    const jawInward = (preview: typeof both) => preview.faceShapeTransform!.bands[0].inward[8];

    expect(jawInward(both)).toBeCloseTo(jawInward(width) + jawInward(small), 6);
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

  it.each(["blob:http://127.0.0.1:15175/local-image", "/api/v2/assets/private-image-id"])("loads private image URL %s through the browser decoder", async (url) => {
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

    await stage.setImageUrl(url);

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

  it("waits for the selected image before exporting instead of returning an empty canvas", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    let resolveLoad!: (texture: {width:number;height:number}) => void;
    pixiMocks.assetsLoad.mockReturnValue(new Promise(resolve=>{resolveLoad=resolve;}));
    const blob=new Blob(["rendered"],{type:"image/png"});
    const toBlob=vi.spyOn(HTMLCanvasElement.prototype,"toBlob").mockImplementation(callback=>callback(blob));
    const stage=await mountPixiStage(document.createElement("div"));
    const loading=stage.setImageUrl("/delayed.png");
    pixiMocks.extractCanvas.mockImplementation(() => document.createElement("canvas"));
    const exporting=stage.exportImage();
    await Promise.resolve();
    expect(toBlob).not.toHaveBeenCalled();
    resolveLoad({width:120,height:100});
    await loading;
    expect(await exporting).toBe(blob);
    stage.destroy();toBlob.mockRestore();
  });
  it("exports source dimensions with the visible deformation and leaves the preview untouched", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    pixiMocks.assetsLoad.mockResolvedValue({ width: 1536, height: 1024 });
    const blob = new Blob(["native"], { type: "image/png" });
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback => callback(blob));
    pixiMocks.extractCanvas.mockImplementation(() => document.createElement("canvas"));
    const stage = await mountPixiStage(document.createElement("div"));
    await stage.setImageUrl("/source.png");
    const preview = pixiMocks.meshes[0];
    const beforeEdit = preview.geometry.positions.slice();
    stage.applyRecipe(updateEyeControl(createEmptyRecipe(), "eyeVertical", 1));
    const visible = preview.geometry.positions.slice();
    expect(visible).not.toEqual(beforeEdit);
    const position = { x: preview.x, y: preview.y, scale: preview.scale.x };
    expect(await stage.exportImage()).toBe(blob);
    const options = pixiMocks.extractCanvas.mock.calls[0][0];
    expect(options.frame).toEqual({ x: 0, y: 0, width: 1536, height: 1024 });
    expect(options.resolution).toBe(1);
    expect(options.target.geometry.positions).toEqual(visible);
    expect(options.target.geometry.positions).not.toBe(preview.geometry.positions);
    expect(options.target.scale.x).toBe(1);
    expect(options.target.x).toBe(0);
    expect(options.target.destroy).toHaveBeenCalledWith({ texture: false, textureSource: false });
    expect(preview.geometry.positions).toEqual(visible);
    expect({ x: preview.x, y: preview.y, scale: preview.scale.x }).toEqual(position);
    stage.destroy();
  });

  it("rejects saving when the graphics renderer fell back to an unedited DOM image", async () => {
    pixiMocks.init.mockRejectedValueOnce(new Error("WebGL unavailable"));
    const host = document.createElement("div");
    const stage = await mountPixiStage(host);
    await expect(stage.setImageUrl("/source.png")).rejects.toThrow("graphics renderer");
    stage.applyRecipe(updateEyeControl(createEmptyRecipe(), "eyeVertical", 1));
    await expect(stage.exportImage()).rejects.toThrow("graphics renderer");
    expect(pixiMocks.extractCanvas).not.toHaveBeenCalled();
    stage.destroy();
  });

  it("freezes the visible legacy deformation then replays identical vertices in any viewport", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    pixiMocks.assetsLoad.mockResolvedValue({ width: 1536, height: 1024 });
    const stage = await mountPixiStage(document.createElement("div"));
    await stage.setImageUrl("/source.png");
    let recipe = updateFaceControl(createEmptyRecipe(), "faceWidth", -.2);
    recipe = updateEyeControl(recipe, "eyeVertical", .07);
    recipe = updateMouthControl(recipe, "mouthSmile", .04);
    recipe = updateLiquifyBrush(recipe, { mode: "warp", radius: 72, strength: .3,
      x: .4, y: .6, deltaX: .04, deltaY: -.02 });
    stage.applyRecipe(recipe);
    const legacy = pixiMocks.meshes[0].geometry.positions.slice();
    const anchored = anchorRecipeToImage(recipe, 1536, 1024, 1536 / 300);
    stage.applyRecipe(anchored);
    const stable = pixiMocks.meshes[0].geometry.positions.slice();
    expect(Math.max(...stable.map((value, index) => Math.abs(value - legacy[index])))).toBeLessThan(.0003);
    const renderer = pixiMocks.applications[0].renderer;
    for (const [width, height] of [[390, 600], [1440, 900], [250, 150]]) {
      renderer.width = width; renderer.height = height;
      pixiMocks.meshes[0].scale.set(Math.min(width / 1536, height / 1024), Math.min(width / 1536, height / 1024));
      stage.applyRecipe(anchored);
      expect(pixiMocks.meshes[0].geometry.positions).toEqual(stable);
    }
    stage.destroy();
  });

  it("rejects a recipe anchored to a different image", async () => {
    vi.stubGlobal("ResizeObserver", ResizeObserverStub);
    const stage = await mountPixiStage(document.createElement("div"));
    await stage.setImageUrl("/source.png");
    expect(() => stage.applyRecipe(anchorRecipeToImage(createEmptyRecipe(), 2048, 1024)))
      .toThrow("do not match");
    stage.destroy();
  });

});

