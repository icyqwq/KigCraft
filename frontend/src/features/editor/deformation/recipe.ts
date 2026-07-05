import { completeLandmarks, createDefaultLandmarks, type ManualLandmarkKey, type ManualLandmarks } from "./landmarks";

export type FaceControlKey =
  | "faceWidth"
  | "faceLength"
  | "midFaceLength"
  | "smallFace"
  | "cheekbone"
  | "chinLength"
  | "chinPoint"
  | "vLine"
  | "jawAngle";

export type EyeControlKey =
  | "eyeSize"
  | "eyeHeight"
  | "eyeWidth"
  | "eyeDistance"
  | "eyeVertical"
  | "eyeTilt"
  | "eyeRegionScale";

export type MouthControlKey =
  | "mouthHorizontal"
  | "mouthVertical"
  | "mouthWidth"
  | "mouthSize"
  | "mouthSmile";

export type LiquifyMode = "expand" | "shrink" | "push-left" | "push-right" | "push-up" | "push-down" | "warp" | "scale";

export type LiquifyStroke = {
  x: number;
  y: number;
  radius: number;
  strength: number;
  mode: LiquifyMode;
  deltaX?: number;
  deltaY?: number;
  scale?: number;
};

export type AnnotationMarkKind = "callout" | "box" | "pin" | "arrow" | "rect" | "text";

export type AnnotationMark = {
  id: string;
  kind: AnnotationMarkKind;
  x: number;
  y: number;
  width: number;
  height: number;
  endX: number;
  endY: number;
  note: string;
  color?: string;
  fontSize?: number;
  strokeWidth?: number;
  text?: string;
};

export type DetailRegion = {
  id: string;
  x: number;
  y: number;
  radius: number;
};

export type DetailSettings = {
  preserveSkinTexture: boolean;
  preserveHairEdges: boolean;
  protectBangs: boolean;
  protectAccessories: boolean;
  manualLandmarkEdit: boolean;
  calibrationMode: boolean;
  accessoryNote: string;
  refineIntensity: number;
  regions: DetailRegion[];
};

export type EditRecipe = {
  face: Record<FaceControlKey, number>;
  eyes: Record<EyeControlKey, number>;
  mouth: Record<MouthControlKey, number>;
  liquify: LiquifyStroke[];
  annotations: AnnotationMark[];
  details: DetailSettings;
  landmarks?: ManualLandmarks;
};

const faceControlKeys = [
  "faceWidth",
  "faceLength",
  "midFaceLength",
  "smallFace",
  "cheekbone",
  "chinLength",
  "chinPoint",
  "vLine",
  "jawAngle",
] as const satisfies readonly FaceControlKey[];

const eyeControlKeys = [
  "eyeSize",
  "eyeHeight",
  "eyeWidth",
  "eyeDistance",
  "eyeVertical",
  "eyeTilt",
  "eyeRegionScale",
] as const satisfies readonly EyeControlKey[];

const mouthControlKeys = [
  "mouthHorizontal",
  "mouthVertical",
  "mouthWidth",
  "mouthSize",
  "mouthSmile",
] as const satisfies readonly MouthControlKey[];

export const defaultEyeControlValues = {
  eyeDistance: 0,
  eyeHeight: 0,
  eyeRegionScale: 20,
  eyeSize: 0,
  eyeTilt: 0,
  eyeVertical: 0,
  eyeWidth: 0,
} as const satisfies Record<EyeControlKey, number>;

export type ControlRange = { max: number; min: number; precision: number };

export const faceControlRanges = {
  cheekbone: { max: 0.4, min: -0.4, precision: 3 },
  chinLength: { max: 0.4, min: -0.4, precision: 3 },
  chinPoint: { max: 0.4, min: -0.4, precision: 3 },
  faceLength: { max: 0.6, min: -0.6, precision: 3 },
  faceWidth: { max: 0.4, min: -0.4, precision: 3 },
  jawAngle: { max: 0.4, min: -0.4, precision: 3 },
  midFaceLength: { max: 0.4, min: -0.4, precision: 3 },
  smallFace: { max: 0.4, min: 0, precision: 3 },
  vLine: { max: 0.4, min: 0, precision: 3 },
} as const satisfies Record<FaceControlKey, ControlRange>;
export const eyeControlRanges = {
  eyeDistance: { max: 0.1, min: -0.1, precision: 4 },
  eyeHeight: { max: 0.6, min: -0.6, precision: 4 },
  eyeRegionScale: { max: 120, min: -80, precision: 0 },
  eyeSize: { max: 0.6, min: -0.6, precision: 4 },
  eyeTilt: { max: 1, min: -1, precision: 4 },
  eyeVertical: { max: 0.1, min: -0.1, precision: 4 },
  eyeWidth: { max: 1, min: -1, precision: 4 },
} as const satisfies Record<EyeControlKey, ControlRange>;

export const mouthControlRanges = {
  mouthHorizontal: { max: 0.05, min: -0.05, precision: 4 },
  mouthVertical: { max: 0.06, min: -0.06, precision: 4 },
  mouthWidth: { max: 0.45, min: -0.45, precision: 4 },
  mouthSize: { max: 0.35, min: -0.35, precision: 4 },
  mouthSmile: { max: 0.08, min: -0.08, precision: 4 },
} as const satisfies Record<MouthControlKey, ControlRange>;

function createZeroedRecord<Key extends string>(keys: readonly Key[]): Record<Key, number> {
  return Object.fromEntries(keys.map((key) => [key, 0])) as Record<Key, number>;
}
function createDefaultDetails(): DetailSettings {
  return {
    accessoryNote: "",
    calibrationMode: false,
    manualLandmarkEdit: false,
    preserveHairEdges: true,
    preserveSkinTexture: true,
    protectAccessories: true,
    protectBangs: true,
    refineIntensity: 0.35,
    regions: [],
  };
}

export function createEmptyRecipe(): EditRecipe {
  return {
    face: createZeroedRecord(faceControlKeys),
    eyes: { ...defaultEyeControlValues },
    mouth: createZeroedRecord(mouthControlKeys),
    liquify: [],
    annotations: [],
    details: createDefaultDetails(),
    landmarks: undefined,
  };
}

export function normalizeEditRecipe(recipe: EditRecipe | Partial<EditRecipe> | undefined): EditRecipe {
  const emptyRecipe = createEmptyRecipe();
  if (!recipe) return emptyRecipe;
  const incomingEyes = recipe.eyes as Partial<Record<EyeControlKey, number>> | undefined;
  const shouldMapLegacyEyeHeight =
    incomingEyes?.eyeSize === undefined && typeof incomingEyes?.eyeHeight === "number";
  const mergedEyes = shouldMapLegacyEyeHeight
    ? {
        ...emptyRecipe.eyes,
        ...incomingEyes,
        eyeHeight: 0,
        eyeSize: incomingEyes.eyeHeight as number,
      }
    : {
        ...emptyRecipe.eyes,
        ...incomingEyes,
      };

  return {
    face: clampFaceControls({ ...emptyRecipe.face, ...recipe.face }),
    eyes: clampEyeControls(mergedEyes),
    mouth: clampMouthControls({ ...emptyRecipe.mouth, ...recipe.mouth }),
    liquify: Array.isArray(recipe.liquify) ? recipe.liquify : [],
    annotations: Array.isArray(recipe.annotations) ? recipe.annotations : [],
    details: {
      ...emptyRecipe.details,
      ...recipe.details,
      regions: Array.isArray(recipe.details?.regions) ? recipe.details.regions : [],
    },
    landmarks: recipe.landmarks ? completeLandmarks(recipe.landmarks) : undefined,
  };
}

function clampControlRange(value: number, range: { max: number; min: number; precision: number }) {
  return Math.min(range.max, Math.max(range.min, Number(value.toFixed(range.precision))));
}

function clampFaceControls(values: Record<FaceControlKey, number>) {
  return Object.fromEntries(
    faceControlKeys.map((key) => [key, clampControlRange(values[key], faceControlRanges[key])]),
  ) as Record<FaceControlKey, number>;
}

function clampEyeControls(values: Record<EyeControlKey, number>) {
  return Object.fromEntries(
    eyeControlKeys.map((key) => [key, clampControlRange(values[key], eyeControlRanges[key])]),
  ) as Record<EyeControlKey, number>;
}

function clampMouthControls(values: Record<MouthControlKey, number>) {
  return Object.fromEntries(
    mouthControlKeys.map((key) => [key, clampControlRange(values[key], mouthControlRanges[key])]),
  ) as Record<MouthControlKey, number>;
}

export function updateFaceControl(recipe: EditRecipe, key: FaceControlKey, value: number): EditRecipe {
  return {
    ...recipe,
    face: {
      ...recipe.face,
      [key]: clampControlRange(value, faceControlRanges[key]),
    },
  };
}

export function updateEyeControl(recipe: EditRecipe, key: EyeControlKey, value: number): EditRecipe {
  return {
    ...recipe,
    eyes: {
      ...recipe.eyes,
      [key]: clampControlRange(value, eyeControlRanges[key]),
    },
  };
}

export function updateMouthControl(recipe: EditRecipe, key: MouthControlKey, value: number): EditRecipe {
  return {
    ...recipe,
    mouth: {
      ...recipe.mouth,
      [key]: clampControlRange(value, mouthControlRanges[key]),
    },
  };
}

function clampNormalizedCoordinate(value: number) {
  return Math.min(1, Math.max(0, Number(value.toFixed(4))));
}

function clampAnnotationSize(value: number) {
  return Math.min(1, Math.max(0.01, Number(value.toFixed(4))));
}

function clampAnnotationFontSize(value: number) {
  return Math.min(72, Math.max(12, Math.round(value)));
}

function createAnnotationId(recipe: EditRecipe) {
  return `annotation-${recipe.annotations.length + 1}`;
}

function clampRadius(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, Number(value.toFixed(4))));
}

function clampSignedScale(value: number) {
  return Math.min(1, Math.max(-1, Number(value.toFixed(2))));
}

function clampNormalizedDelta(value: number) {
  return Math.min(0.35, Math.max(-0.35, Number(value.toFixed(4))));
}

export function createLiquifyStrokeFromNormalizedPoint({
  x,
  y,
  radius,
  strength,
  mode,
  deltaX,
  deltaY,
  scale,
}: LiquifyStroke): LiquifyStroke {
  return {
    ...(deltaX === undefined ? {} : { deltaX: clampNormalizedDelta(deltaX) }),
    ...(deltaY === undefined ? {} : { deltaY: clampNormalizedDelta(deltaY) }),
    x: clampNormalizedCoordinate(x),
    y: clampNormalizedCoordinate(y),
    ...(scale === undefined ? {} : { scale: clampSignedScale(scale) }),
    radius: clampRadius(radius, 4, 240),
    strength: clampRadius(strength, 0, 1),
    mode,
  };
}

export function createLiquifyWarpStrokeFromDrag({
  from,
  radius,
  strength = 1,
  to,
}: {
  from: { x: number; y: number };
  radius: number;
  strength?: number;
  to: { x: number; y: number };
}): LiquifyStroke {
  const deltaX = clampNormalizedDelta(to.x - from.x);
  const deltaY = clampNormalizedDelta(to.y - from.y);
  const distance = Math.hypot(deltaX, deltaY);

  return createLiquifyStrokeFromNormalizedPoint({
    deltaX,
    deltaY,
    mode: "warp",
    radius,
    strength: distance > 0 ? strength : 0,
    x: (from.x + to.x) / 2,
    y: (from.y + to.y) / 2,
  });
}

export function updateLiquifyBrush(recipe: EditRecipe, stroke: LiquifyStroke): EditRecipe {
  return {
    ...recipe,
    liquify: [...recipe.liquify, stroke],
  };
}

export function updateLiquifyScaleBrush(
  recipe: EditRecipe,
  brush: { radius: number; scale: number; x?: number; y?: number },
): EditRecipe {
  const currentScaleStroke = [...recipe.liquify].reverse().find((stroke) => stroke.mode === "scale");
  const nextStroke = createLiquifyStrokeFromNormalizedPoint({
    mode: "scale",
    radius: brush.radius,
    scale: brush.scale,
    strength: Math.abs(brush.scale),
    x: brush.x ?? currentScaleStroke?.x ?? 0.5,
    y: brush.y ?? currentScaleStroke?.y ?? 0.5,
  });

  return {
    ...recipe,
    liquify: [...recipe.liquify, nextStroke],
  };
}

export function updateManualLandmark(
  recipe: EditRecipe,
  key: ManualLandmarkKey,
  point: { x: number; y: number },
): EditRecipe {
  const landmarks = completeLandmarks(recipe.landmarks ?? createDefaultLandmarks(1, 1));

  return {
    ...recipe,
    landmarks: {
      ...landmarks,
      [key]: {
        x: clampNormalizedCoordinate(point.x),
        y: clampNormalizedCoordinate(point.y),
      },
    },
  };
}

export function addAnnotationMark(
  recipe: EditRecipe,
  mark: Pick<AnnotationMark, "x" | "y"> &
    Partial<
      Pick<
        AnnotationMark,
        "color" | "endX" | "endY" | "fontSize" | "height" | "kind" | "note" | "strokeWidth" | "text" | "width"
      >
    >,
): EditRecipe {
  const x = clampNormalizedCoordinate(mark.x);
  const y = clampNormalizedCoordinate(mark.y);
  const kind = mark.kind ?? "callout";
  const width = clampAnnotationSize(
    mark.width ?? (kind === "text" ? 0.5 : kind === "rect" || kind === "box" ? 0.18 : 0.01),
  );
  const height = clampAnnotationSize(
    mark.height ?? (kind === "text" ? 0.08 : kind === "rect" || kind === "box" ? 0.12 : 0.01),
  );

  return {
    ...recipe,
    annotations: [
      ...recipe.annotations,
      {
        color: mark.color ?? "#ef4444",
        endX: clampNormalizedCoordinate(mark.endX ?? x + 0.12),
        endY: clampNormalizedCoordinate(mark.endY ?? y - 0.1),
        fontSize: clampAnnotationFontSize(mark.fontSize ?? 24),
        height,
        id: createAnnotationId(recipe),
        kind,
        note: mark.note ?? "",
        strokeWidth: Math.min(12, Math.max(2, mark.strokeWidth ?? 4)),
        text: mark.text ?? "",
        width,
        x,
        y,
      },
    ],
  };
}

export function updateAnnotationMark(
  recipe: EditRecipe,
  annotationId: string,
  patch: Partial<Omit<AnnotationMark, "id">>,
): EditRecipe {
  return {
    ...recipe,
    annotations: recipe.annotations.map((annotation) => {
      if (annotation.id !== annotationId) return annotation;

      return {
        ...annotation,
        ...patch,
        ...(patch.endX === undefined ? {} : { endX: clampNormalizedCoordinate(patch.endX) }),
        ...(patch.endY === undefined ? {} : { endY: clampNormalizedCoordinate(patch.endY) }),
        ...(patch.fontSize === undefined ? {} : { fontSize: clampAnnotationFontSize(patch.fontSize) }),
        ...(patch.height === undefined ? {} : { height: clampAnnotationSize(patch.height) }),
        ...(patch.strokeWidth === undefined ? {} : { strokeWidth: Math.min(12, Math.max(2, patch.strokeWidth)) }),
        ...(patch.width === undefined ? {} : { width: clampAnnotationSize(patch.width) }),
        ...(patch.x === undefined ? {} : { x: clampNormalizedCoordinate(patch.x) }),
        ...(patch.y === undefined ? {} : { y: clampNormalizedCoordinate(patch.y) }),
      };
    }),
  };
}

export function updateAnnotationNote(recipe: EditRecipe, annotationId: string, note: string): EditRecipe {
  return updateAnnotationMark(recipe, annotationId, { note });
}

export function moveAnnotationMark(
  recipe: EditRecipe,
  annotationId: string,
  point: Pick<AnnotationMark, "x" | "y">,
): EditRecipe {
  const x = clampNormalizedCoordinate(point.x);
  const y = clampNormalizedCoordinate(point.y);

  return {
    ...recipe,
    annotations: recipe.annotations.map((annotation) => {
      if (annotation.id !== annotationId) return annotation;

      const endOffsetX = annotation.endX - annotation.x;
      const endOffsetY = annotation.endY - annotation.y;

      return {
        ...annotation,
        endX: clampNormalizedCoordinate(x + endOffsetX),
        endY: clampNormalizedCoordinate(y + endOffsetY),
        x,
        y,
      };
    }),
  };
}

export function removeAnnotationMark(recipe: EditRecipe, annotationId: string): EditRecipe {
  return {
    ...recipe,
    annotations: recipe.annotations.filter((annotation) => annotation.id !== annotationId),
  };
}

export function compactRecipeAnnotations(recipe: EditRecipe): EditRecipe {
  return {
    ...recipe,
    annotations: recipe.annotations
      .filter((annotation) => {
        const note = annotation.note.trim();
        const text = (annotation.text ?? "").trim();
        if (annotation.kind === "pin" || annotation.kind === "callout") return note.length > 0;
        if (annotation.kind === "text") return text.length > 0 || note.length > 0;
        return true;
      })
      .map((annotation, index) => ({
        ...annotation,
        id: `annotation-${index + 1}`,
        note: annotation.note.trim(),
        text: annotation.text?.trim() ?? "",
      })),
  };
}

function buildAnnotationPromptLegacy(annotations: readonly AnnotationMark[]): string {
  return annotations
    .map((annotation, index) => {
      const xPercent = Math.round(clampNormalizedCoordinate(annotation.x) * 100);
      const yPercent = Math.round(clampNormalizedCoordinate(annotation.y) * 100);

      return `标注 ${index + 1}: ${xPercent}%, ${yPercent}%, ${annotation.note.trim()}`;
    })
    .join("\n");
}

export function buildAnnotationPrompt(annotations: readonly AnnotationMark[]): string {
  return annotations
    .map((annotation, index) => {
      const xPercent = Math.round(clampNormalizedCoordinate(annotation.x) * 100);
      const yPercent = Math.round(clampNormalizedCoordinate(annotation.y) * 100);
      const endXPercent = Math.round(clampNormalizedCoordinate(annotation.endX) * 100);
      const endYPercent = Math.round(clampNormalizedCoordinate(annotation.endY) * 100);
      const widthPercent = Math.round(clampAnnotationSize(annotation.width) * 100);
      const heightPercent = Math.round(clampAnnotationSize(annotation.height) * 100);
      const note = annotation.note.trim();
      const text = (annotation.text ?? "").trim();

      if (annotation.kind === "arrow") {
        return `标注 ${index + 1}: 箭头从 ${xPercent}%, ${yPercent}% 指向 ${endXPercent}%, ${endYPercent}%${note ? `, ${note}` : ""}`;
      }

      if (annotation.kind === "rect" || annotation.kind === "box") {
        return `标注 ${index + 1}: 框选区域 ${xPercent}%, ${yPercent}%, 宽 ${widthPercent}%, 高 ${heightPercent}%${note ? `, ${note}` : ""}`;
      }

      if (annotation.kind === "text") {
        return `标注 ${index + 1}: 文字 "${text || note}" 位于 ${xPercent}%, ${yPercent}%`;
      }

      return `标注 ${index + 1}: ${xPercent}%, ${yPercent}%, ${note}`;
    })
    .join("\n");
}

export function updateDetailSetting<Key extends keyof DetailSettings>(
  recipe: EditRecipe,
  key: Key,
  value: DetailSettings[Key],
): EditRecipe {
  return {
    ...recipe,
    details: {
      ...recipe.details,
      [key]: value,
    },
  };
}

export function addDetailRegion(
  recipe: EditRecipe,
  region: Pick<DetailRegion, "x" | "y"> & Partial<Pick<DetailRegion, "radius">>,
): EditRecipe {
  return {
    ...recipe,
    details: {
      ...recipe.details,
      regions: [
        ...recipe.details.regions,
        {
          id: `detail-${recipe.details.regions.length + 1}`,
          radius: clampRadius(region.radius ?? 0.11, 0.03, 0.32),
          x: clampNormalizedCoordinate(region.x),
          y: clampNormalizedCoordinate(region.y),
        },
      ],
    },
  };
}
