import { ActionIcon, Alert, Box, Button, Divider, Group, Paper, Slider, Stack, Text, Textarea, TextInput, Title, Tooltip, useMediaQuery } from "../../ui/mui";
import {
  IconArrowBackUp,
  IconArrowForwardUp,
  IconArrowUpRight,
  IconCircleDot,
  IconRectangle,
  IconPhoto,
  IconTrash,
  IconTypography,
} from "@tabler/icons-react";
import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type SyntheticEvent,
} from "react";
import { AnnotationLayer } from "./components/AnnotationLayer";
import { EditorToolbar } from "./components/EditorToolbar";
import { EditorToolRail, type EditorTool } from "./components/EditorToolRail";
import { EyeControls } from "./components/EyeControls";
import { FaceControls } from "./components/FaceControls";
import { LocalMaskLayer } from "./components/LocalMaskLayer";
import { LiquifyControls, type LiquifyToolMode } from "./components/LiquifyControls";
import { MouthControls } from "./components/MouthControls";
import { drawAnnotationsToCanvas } from "./deformation/annotationExport";
import {
  addAnnotationMark,
  buildAnnotationPrompt,
  compactRecipeAnnotations,
  createLiquifyStrokeFromNormalizedPoint,
  createLiquifyWarpStrokeFromDrag,
  createEmptyRecipe,
  defaultEyeControlValues,
  moveAnnotationMark,
  normalizeEditRecipe,
  removeAnnotationMark,
  updateAnnotationMark,
  updateAnnotationNote,
  updateEyeControl,
  updateFaceControl,
  updateLiquifyBrush,
  updateManualLandmark,
  updateMouthControl,
  type EditRecipe,
  type AnnotationMark,
  type EyeControlKey,
  type LiquifyStroke,
  type MouthControlKey,
} from "./deformation/recipe";
import { createDefaultLandmarks, type LandmarkPoint, type ManualLandmarkKey, type ManualLandmarks } from "./deformation/landmarks";
import {
  detectAnimeLandmarks,
  warmupAnimeLandmarkDetector,
  type AnimeLandmarkDebugBox,
  type AnimeLandmarkDebugInfo,
} from "./deformation/animeLandmarkDetector";
import { calculateRecipePreview, mountPixiStage, type PixiStageHandle } from "./deformation/pixiStage";
import {
  exportLocalMaskBlob,
  hasLocalMaskPaint,
  type EditorLocalGeneratePayload,
  type EditorLocalReferenceOption,
  type LocalMaskMode,
  type LocalMaskStroke,
} from "./localGeneration";

export type { EditorLocalGeneratePayload, EditorLocalReferenceOption } from "./localGeneration";

export type EditorWorkspaceProps = {
  availableTools?: EditorTool[];
  candidateIndex: number;
  imageHeight?: number;
  initialLandmarks?: ManualLandmarks | null;
  imageUrl?: string;
  imageWidth?: number;
  isRegenerating?: boolean;
  localReferenceOptions?: EditorLocalReferenceOption[];
  recipe?: EditRecipe;
  regenerateLabel?: string;
  secondaryRegenerateLabel?: string;
  showRegenerateActions?: boolean;
  onRecipeChange?: (recipe: EditRecipe) => void;
  onClearImage?: () => void;
  onLocalGenerate?: (payload: EditorLocalGeneratePayload) => void | Promise<void>;
  onRegenerate?: (payload: EditorRegeneratePayload) => void | Promise<void>;
  onSecondaryRegenerate?: (payload: EditorRegeneratePayload) => void | Promise<void>;
  onSave?: (payload: EditorImageSavePayload) => void | Promise<void>;
};

export type EditorRegeneratePayload = {
  annotationPrompt: string;
  annotatedImageBlob?: Blob;
  editedImageBlob: Blob;
  promptNote?: string;
  extraReference?: {
    description: string;
    file: File;
  };
  recipe: EditRecipe;
};

export type EditorImageSavePayload = {
  annotationPrompt: string;
  fileName: string;
  imageBlob: Blob;
  recipe: EditRecipe;
};

const toolDetails: Partial<Record<EditorTool, { description: string; title: string }>> = {
  annotation: {
    title: "标注",
    description: "圈选需要保留、修正或重点观察的区域。",
  },
  face: {
    title: "脸型",
    description: "调整脸宽、下颌、下巴和 V 脸方向。",
  },
  eyes: {
    title: "眼睛",
    description: "调整眼睛大小、高度、宽度、距离、上下位置和倾斜角度。",
  },
  mouth: {
    title: "嘴巴",
    description: "调整嘴巴位置、宽度、大小和微笑弧度。",
  },
  liquify: {
    title: "液化",
    description: "用变形笔刷或局部缩放调整画面局部形状。",
  },
};

const defaultBrushRadius = 72;
const defaultLiquifyScaleAmount = 0;
const defaultLiquifyWarpStrength = 0.2;
const defaultLiquifyToolMode: LiquifyToolMode = "warp";
const defaultEditorTools: EditorTool[] = ["annotation", "face", "eyes", "mouth", "liquify", "local-generate"];
const minEditorZoom = 0.4;
const maxEditorZoom = 2.2;
const touchLongPressMs = 560;
const touchMoveTolerance = 10;

type LiquifyHistoryEntry = {
  after: LiquifyStroke[];
  before: LiquifyStroke[];
  key?: string;
};

type AnnotationHistoryEntry = {
  after: AnnotationMark[];
  before: AnnotationMark[];
  key?: string;
};

type ViewportRect = {
  height: number;
  left: number;
  top: number;
  width: number;
};

type AnnotationToolMode = "pin" | "arrow" | "rect" | "text";
type AnnotationResizeHandle = "end" | "ne" | "nw" | "se" | "sw";
type StagePointerPoint = {
  clientX: number;
  clientY: number;
  pointerType: string;
};

export type EditorWorkspaceHandle = {
  regenerate: () => Promise<void>;
  secondaryRegenerate: () => Promise<void>;
};
type PendingTouchAction = {
  clientX: number;
  clientY: number;
  longPressTimer: number;
  menuOpened: boolean;
  moved: boolean;
  point: { x: number; y: number };
  pointerId: number;
  startedEdit: boolean;
};
type ViewportGesture = {
  startCenter: { x: number; y: number };
  startDistance: number;
  startPan: { x: number; y: number };
  startZoom: number;
};

function clampEditorZoom(value: number) {
  return Math.min(maxEditorZoom, Math.max(minEditorZoom, Number(value.toFixed(2))));
}
function recipesMatch(left: EditRecipe, right: EditRecipe) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function calculateContainViewport(
  containerWidth: number,
  containerHeight: number,
  contentWidth: number,
  contentHeight: number,
): ViewportRect {
  if (containerWidth <= 0 || containerHeight <= 0 || contentWidth <= 0 || contentHeight <= 0) {
    return { height: containerHeight, left: 0, top: 0, width: containerWidth };
  }

  const scale = Math.min(containerWidth / contentWidth, containerHeight / contentHeight);
  const width = contentWidth * scale;
  const height = contentHeight * scale;

  return {
    height,
    left: (containerWidth - width) / 2,
    top: (containerHeight - height) / 2,
    width,
  };
}

function normalizeDebugBox(box: AnimeLandmarkDebugBox, imageWidth: number, imageHeight: number) {
  const safeWidth = Math.max(1, imageWidth);
  const safeHeight = Math.max(1, imageHeight);

  return {
    height: Number((box.height / safeHeight).toFixed(4)),
    score: box.score,
    width: Number((box.width / safeWidth).toFixed(4)),
    x: Number((box.x / safeWidth).toFixed(4)),
    y: Number((box.y / safeHeight).toFixed(4)),
  };
}

function canvasToPngBlob(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((resolve, reject) => {
    canvas.toBlob((blob) => {
      if (blob) {
        resolve(blob);
        return;
      }
      reject(new Error("Unable to export editor image"));
    }, "image/png");
  });
}

function downloadBlob(blob: Blob, fileName: string) {
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = fileName;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function loadBlobImage(blob: Blob): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    if (typeof URL.createObjectURL !== "function") {
      reject(new Error("Blob image loading is not available"));
      return;
    }

    const objectUrl = URL.createObjectURL(blob);
    const image = new Image();
    image.onload = () => {
      URL.revokeObjectURL(objectUrl);
      resolve(image);
    };
    image.onerror = () => {
      URL.revokeObjectURL(objectUrl);
      reject(new Error("Unable to load editor export for annotation overlay"));
    };
    image.src = objectUrl;
  });
}

export const EditorWorkspace = forwardRef<EditorWorkspaceHandle, EditorWorkspaceProps>(function EditorWorkspace({
  availableTools,
  candidateIndex,
  imageHeight,
  initialLandmarks,
  imageUrl,
  imageWidth,
  isRegenerating = false,
  localReferenceOptions = [],
  recipe: controlledRecipe,
  regenerateLabel,
  secondaryRegenerateLabel,
  showRegenerateActions = true,
  onRecipeChange,
  onClearImage,
  onLocalGenerate,
  onRegenerate,
  onSecondaryRegenerate,
  onSave,
}: EditorWorkspaceProps, ref) {
  const isMobileEditor = useMediaQuery("(max-width: 768px)", false);
  const [activeTool, setActiveTool] = useState<EditorTool>("annotation");
  const [recipe, setRecipe] = useState(() => normalizeEditRecipe(controlledRecipe ?? createEmptyRecipe()));
  const [liquifyToolMode, setLiquifyToolMode] = useState<LiquifyToolMode>(defaultLiquifyToolMode);
  const [brushRadius, setBrushRadius] = useState(defaultBrushRadius);
  const [liquifyScaleAmount, setLiquifyScaleAmount] = useState(defaultLiquifyScaleAmount);
  const [liquifyWarpStrength, setLiquifyWarpStrength] = useState(defaultLiquifyWarpStrength);
  const [liquifyBrushPreview, setLiquifyBrushPreview] = useState<{
    active: boolean;
    mode: LiquifyToolMode;
    radius: number;
    x: number;
    y: number;
  } | null>(null);
  const [pendingScaleStroke, setPendingScaleStroke] = useState<LiquifyStroke | null>(null);
  const [scaleBrushPoint, setScaleBrushPoint] = useState<{ x: number; y: number } | null>(null);
  const [isScaleBrushDragging, setIsScaleBrushDragging] = useState(false);
  const [compareOriginal, setCompareOriginal] = useState(false);
  const [showLandmarkControls, setShowLandmarkControls] = useState(true);
  const [landmarkDebugMode, setLandmarkDebugMode] = useState(false);
  const [landmarkDebugInfo, setLandmarkDebugInfo] = useState<AnimeLandmarkDebugInfo | null>(null);
  const [temporarilyHideLandmarks, setTemporarilyHideLandmarks] = useState(false);
  const [showLandmarkHint, setShowLandmarkHint] = useState(true);
  const [isDetectingLandmarks, setIsDetectingLandmarks] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [detectedImageSize, setDetectedImageSize] = useState<{ height: number; width: number } | null>(null);
  const [imageLoadVersion, setImageLoadVersion] = useState(0);
  const [secondaryLandmarks, setSecondaryLandmarks] = useState<LandmarkPoint[]>([]);
  const [stageViewportSize, setStageViewportSize] = useState({ height: 0, width: 0 });
  const [selectedAnnotationId, setSelectedAnnotationId] = useState<string | null>(null);
  const [annotationToolMode, setAnnotationToolMode] = useState<AnnotationToolMode>("pin");
  const [annotationColor, setAnnotationColor] = useState("#ef4444");
  const [annotationFontSize, setAnnotationFontSize] = useState(24);
  const [selectedLandmarkKey, setSelectedLandmarkKey] = useState<ManualLandmarkKey | null>(null);
  const [extraReferenceFile, setExtraReferenceFile] = useState<File | null>(null);
  const [extraReferencePreviewUrl, setExtraReferencePreviewUrl] = useState<string | null>(null);
  const [extraReferenceDescription, setExtraReferenceDescription] = useState("");
  const [localMaskMode, setLocalMaskMode] = useState<LocalMaskMode>("brush");
  const [localMaskRadius, setLocalMaskRadius] = useState(36);
  const [localMaskStrokes, setLocalMaskStrokes] = useState<LocalMaskStroke[]>([]);
  const [localMaskBrushPreview, setLocalMaskBrushPreview] = useState<{ radius: number; x: number; y: number } | null>(null);
  const [localEditNote, setLocalEditNote] = useState("");
  const [selectedLocalReferenceKeys, setSelectedLocalReferenceKeys] = useState<string[]>([]);
  const [localUploadedReferenceFile, setLocalUploadedReferenceFile] = useState<File | null>(null);
  const [localUploadedReferenceDescription, setLocalUploadedReferenceDescription] = useState("");
  const [localGenerateError, setLocalGenerateError] = useState<string | null>(null);
  const [saveMenuPosition, setSaveMenuPosition] = useState<{ x: number; y: number } | null>(null);
  const [pixiHostElement, setPixiHostElement] = useState<HTMLDivElement | null>(null);
  const [pixiStageVersion, setPixiStageVersion] = useState(0);
  const [pixiVisualReady, setPixiVisualReady] = useState(false);
  const [pixiStageFailed, setPixiStageFailed] = useState(false);
  const [liquifyHistoryAvailability, setLiquifyHistoryAvailability] = useState({
    canRedo: false,
    canUndo: false,
  });
  const [annotationHistoryAvailability, setAnnotationHistoryAvailability] = useState({
    canRedo: false,
    canUndo: false,
  });
  const baseImageRef = useRef<HTMLImageElement | null>(null);
  const extraReferenceInputRef = useRef<HTMLInputElement | null>(null);
  const imageViewportRef = useRef<HTMLDivElement | null>(null);
  const localMaskActiveStrokeIdRef = useRef<string | null>(null);
  const localReferenceInputRef = useRef<HTMLInputElement | null>(null);
  const pixiStageRef = useRef<PixiStageHandle | null>(null);
  const pixiRecipeFrameRef = useRef<number | null>(null);
  const stageRef = useRef<HTMLDivElement | null>(null);
  const latestImageUrlRef = useRef(imageUrl);
  const latestRecipeRef = useRef(recipe);
  const landmarkDetectionRunRef = useRef(0);
  const landmarkFallbackRef = useRef(false);
  const undoStackRef = useRef<LiquifyHistoryEntry[]>([]);
  const redoStackRef = useRef<LiquifyHistoryEntry[]>([]);
  const activeHistoryKeyRef = useRef<string | null>(null);
  const annotationUndoStackRef = useRef<AnnotationHistoryEntry[]>([]);
  const annotationRedoStackRef = useRef<AnnotationHistoryEntry[]>([]);
  const activeAnnotationHistoryKeyRef = useRef<string | null>(null);
  const activeScaleEditIndexRef = useRef<number | null>(null);
  const liquifyOperationIdRef = useRef(0);
  const activeLiquifyHistoryKeyRef = useRef<string | null>(null);
  const liquifyGestureStartRef = useRef<LiquifyStroke[] | null>(null);
  const currentScaleDragAmountRef = useRef(liquifyScaleAmount);
  const lastStagePointRef = useRef({ x: 0.5, y: 0.5 });
  const lastLiquifyStrokePointRef = useRef<{ x: number; y: number } | null>(null);
  const annotationPendingPointRef = useRef<{
    pointerId: number;
    point: { x: number; y: number };
    startClientX: number;
    startClientY: number;
  } | null>(null);
  const annotationDragRef = useRef<{
    annotationId: string;
    offsetX: number;
    offsetY: number;
    pointerId: number;
  } | null>(null);
  const annotationCreateRef = useRef<{
    annotationId: string;
    kind: "arrow" | "rect";
    pointerId: number;
    start: { x: number; y: number };
  } | null>(null);
  const annotationResizeRef = useRef<{
    annotationId: string;
    handle: AnnotationResizeHandle;
    original: AnnotationMark;
    pointerId: number;
    start: { x: number; y: number };
  } | null>(null);
  const landmarkDragRef = useRef<{
    key: ManualLandmarkKey;
    offsetX: number;
    offsetY: number;
    pointerId: number;
  } | null>(null);
  const liquifyDrawingRef = useRef(false);
  const activeStagePointersRef = useRef<Map<number, StagePointerPoint>>(new Map());
  const pendingTouchActionRef = useRef<PendingTouchAction | null>(null);
  const viewportGestureRef = useRef<ViewportGesture | null>(null);
  const panDragRef = useRef<{
    moved: boolean;
    pointerId: number;
    startClientX: number;
    startClientY: number;
    startPanX: number;
    startPanY: number;
  } | null>(null);
  const suppressNextContextMenuRef = useRef(false);
  const latestZoomRef = useRef(zoom);
  const latestPanRef = useRef(pan);
  const shouldNotifyRecipeRef = useRef(false);
  const enabledTools = useMemo(
    () =>
      (availableTools?.length ? availableTools : defaultEditorTools).filter(
        (tool) => tool !== "local-generate" || Boolean(onLocalGenerate),
      ),
    [availableTools, onLocalGenerate],
  );
  const enabledToolSet = useMemo(() => new Set(enabledTools), [enabledTools]);
  const activeToolDetail = toolDetails[activeTool] ?? {
    title: "局部生成",
    description: "涂抹要修改的区域，提交给 AI 只重做这一块。",
  };
  const canShowLandmarkToggle = enabledToolSet.has("face") || enabledToolSet.has("eyes") || enabledToolSet.has("mouth");
  const effectiveShowLandmarks = canShowLandmarkToggle && showLandmarkControls && !temporarilyHideLandmarks;
  const zoomPercent = Math.round(zoom * 100);
  const canUndo = liquifyHistoryAvailability.canUndo;
  const canRedo = liquifyHistoryAvailability.canRedo;
  const canUndoAnnotation = annotationHistoryAvailability.canUndo;
  const canRedoAnnotation = annotationHistoryAvailability.canRedo;
  const stageWidth = detectedImageSize?.width ?? (imageWidth && imageWidth > 0 ? imageWidth : 960);
  const stageHeight = detectedImageSize?.height ?? (imageHeight && imageHeight > 0 ? imageHeight : 720);
  const stageAspectRatio = `${Math.max(1, stageWidth)} / ${Math.max(1, stageHeight)}`;
  const imageViewportRect = calculateContainViewport(
    stageViewportSize.width,
    stageViewportSize.height,
    stageWidth,
    stageHeight,
  );
  const imageViewportReady = Boolean(detectedImageSize) && imageViewportRect.width > 0 && imageViewportRect.height > 0;
  const hasImage = Boolean(imageUrl);
  const currentLandmarks = recipe.landmarks;
  const normalizedDebugFaceBox = landmarkDebugInfo
    ? normalizeDebugBox(landmarkDebugInfo.faceBox, landmarkDebugInfo.imageWidth, landmarkDebugInfo.imageHeight)
    : null;
  const normalizedDebugHrnetBox = landmarkDebugInfo
    ? normalizeDebugBox(landmarkDebugInfo.hrnetBox, landmarkDebugInfo.imageWidth, landmarkDebugInfo.imageHeight)
    : null;
  const previewRecipe = useMemo(
    () =>
      pendingScaleStroke
        ? {
            ...recipe,
            liquify: [...recipe.liquify, pendingScaleStroke],
          }
        : recipe,
    [pendingScaleStroke, recipe],
  );
  const visibleLiquifyBrushPreview =
    activeTool === "liquify"
      ? liquifyToolMode === "scale" && scaleBrushPoint
        ? {
            active: isScaleBrushDragging,
            mode: "scale" as const,
            radius: brushRadius,
            x: scaleBrushPoint.x,
            y: scaleBrushPoint.y,
          }
        : liquifyBrushPreview ??
          (pendingScaleStroke
            ? {
                active: false,
                mode: "scale" as const,
                radius: pendingScaleStroke.radius,
                x: pendingScaleStroke.x,
                y: pendingScaleStroke.y,
              }
            : null)
      : null;
  const recipePreview = calculateRecipePreview(previewRecipe);
  const fallbackPreviewTransform = [
    `translate(${recipePreview.imageOffset.x + recipePreview.eyeOffset.x}px, ${
      recipePreview.imageOffset.y + recipePreview.eyeOffset.y
    }px)`,
    `scale(${recipePreview.imageScale.x * recipePreview.eyeScale.x}, ${
      recipePreview.imageScale.y * recipePreview.eyeScale.y
    })`,
    `skew(${recipePreview.imageSkew.x + recipePreview.eyeSkew}rad, ${recipePreview.imageSkew.y}rad)`,
  ].join(" ");
  latestImageUrlRef.current = imageUrl;
  currentScaleDragAmountRef.current = liquifyScaleAmount;
  latestZoomRef.current = zoom;
  latestPanRef.current = pan;

  useEffect(() => {
    if (!pixiHostElement) return;

    let cancelled = false;
    let stageHandle: PixiStageHandle | null = null;
    setPixiVisualReady(false);
    setPixiStageFailed(false);

    void mountPixiStage(pixiHostElement)
      .then((stage) => {
        stageHandle = stage;
        if (cancelled) {
          stage.destroy();
          return;
        }

        pixiStageRef.current = stage;
        setPixiStageVersion((current) => current + 1);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        console.warn("Editor Pixi stage failed to mount", error);
        setPixiStageFailed(true);
        setPixiVisualReady(false);
      });

    return () => {
      cancelled = true;
      if (pixiRecipeFrameRef.current !== null) {
        window.cancelAnimationFrame(pixiRecipeFrameRef.current);
        pixiRecipeFrameRef.current = null;
      }
      if (pixiStageRef.current === stageHandle) {
        pixiStageRef.current = null;
      }
      stageHandle?.destroy();
    };
  }, [pixiHostElement]);

  useEffect(() => {
    const stage = pixiStageRef.current;
    if (!stage || !imageUrl) {
      setPixiVisualReady(false);
      return;
    }

    let cancelled = false;
    setPixiVisualReady(false);
    setPixiStageFailed(false);

    void stage
      .setImageUrl(imageUrl)
      .then(() => {
        if (cancelled) return;
        stage.applyRecipe(previewRecipe);
        setPixiVisualReady(true);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        console.warn("Editor Pixi image failed to load", error);
        setPixiStageFailed(true);
        setPixiVisualReady(false);
      });

    return () => {
      cancelled = true;
    };
  }, [imageUrl, pixiStageVersion]);

  useEffect(() => {
    const stage = pixiStageRef.current;
    if (!stage) return;

    if (pixiRecipeFrameRef.current !== null) {
      window.cancelAnimationFrame(pixiRecipeFrameRef.current);
    }

    pixiRecipeFrameRef.current = window.requestAnimationFrame(() => {
      pixiRecipeFrameRef.current = null;
      pixiStageRef.current?.applyRecipe(previewRecipe);
    });

    return () => {
      if (pixiRecipeFrameRef.current !== null) {
        window.cancelAnimationFrame(pixiRecipeFrameRef.current);
        pixiRecipeFrameRef.current = null;
      }
    };
  }, [previewRecipe, pixiStageVersion]);

  useEffect(() => {
    if (controlledRecipe) {
      const nextRecipe = normalizeEditRecipe(controlledRecipe);
      if (recipesMatch(nextRecipe, latestRecipeRef.current)) {
        return;
      }
      setRecipeWithoutHistory(nextRecipe);
    }
  }, [controlledRecipe]);

  useEffect(() => {
    if (enabledToolSet.has(activeTool)) return;
    setActiveTool(enabledTools[0] ?? "annotation");
  }, [activeTool, enabledTools, enabledToolSet]);

  useEffect(() => {
    if (!canShowLandmarkToggle) {
      setShowLandmarkControls(false);
    }
  }, [canShowLandmarkToggle]);

  useEffect(() => {
    const stageElement = stageRef.current;
    if (!stageElement) return;

    const updateStageViewportSize = () => {
      const rect = stageElement.getBoundingClientRect();
      setStageViewportSize({
        height: Math.max(0, rect.height),
        width: Math.max(0, rect.width),
      });
    };

    updateStageViewportSize();

    window.addEventListener("resize", updateStageViewportSize);
    if (typeof ResizeObserver === "undefined") {
      return () => {
        window.removeEventListener("resize", updateStageViewportSize);
      };
    }

    const resizeObserver = new ResizeObserver(updateStageViewportSize);
    resizeObserver.observe(stageElement);

    return () => {
      resizeObserver.disconnect();
      window.removeEventListener("resize", updateStageViewportSize);
    };
  }, []);

  useEffect(() => {
    if (!imageUrl || !showLandmarkHint || !effectiveShowLandmarks || !currentLandmarks) return;
    const timeoutId = window.setTimeout(() => {
      setShowLandmarkHint(false);
    }, 6500);

    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [currentLandmarks, effectiveShowLandmarks, imageUrl, showLandmarkHint]);

  useEffect(() => {
    setShowLandmarkHint(Boolean(imageUrl));
  }, [imageUrl]);

  useEffect(() => {
    if (!canShowLandmarkToggle) return;

    let cancelled = false;
    const warmup = () => {
      if (cancelled) return;
      void warmupAnimeLandmarkDetector();
    };

    if (typeof window.requestIdleCallback === "function") {
      const idleId = window.requestIdleCallback(warmup, { timeout: 1500 });
      return () => {
        cancelled = true;
        window.cancelIdleCallback(idleId);
      };
    }

    const timeoutId = window.setTimeout(warmup, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timeoutId);
    };
  }, [canShowLandmarkToggle]);

  useEffect(() => {
    if (!extraReferencePreviewUrl) return;

    return () => {
      URL.revokeObjectURL(extraReferencePreviewUrl);
    };
  }, [extraReferencePreviewUrl]);

  useEffect(() => {
    latestRecipeRef.current = recipe;
    if (shouldNotifyRecipeRef.current) {
      shouldNotifyRecipeRef.current = false;
      onRecipeChange?.(recipe);
    }
  }, [onRecipeChange, recipe]);

  useEffect(() => {
    landmarkDetectionRunRef.current += 1;
    setDetectedImageSize(null);
    setImageLoadVersion(0);
    setLandmarkDebugInfo(null);
    setSecondaryLandmarks([]);
    setPendingScaleStroke(null);
    setLiquifyBrushPreview(null);
    setScaleBrushPoint(null);
    setLocalMaskBrushPreview(null);
    setLocalMaskStrokes([]);
    setIsScaleBrushDragging(false);
    setLiquifyScaleAmount(defaultLiquifyScaleAmount);
    setZoom(1);
    setPan({ x: 0, y: 0 });
    latestZoomRef.current = 1;
    latestPanRef.current = { x: 0, y: 0 };
    activeScaleEditIndexRef.current = null;
    currentScaleDragAmountRef.current = defaultLiquifyScaleAmount;
    clearRecipeHistory();
    setRecipeWithoutHistory({
      ...latestRecipeRef.current,
      landmarks: initialLandmarks ?? undefined,
    });
    landmarkFallbackRef.current = false;
  }, [imageUrl]);

  useEffect(() => {
    if (!imageUrl || !initialLandmarks) return;
    if (JSON.stringify(latestRecipeRef.current.landmarks ?? null) === JSON.stringify(initialLandmarks)) return;
    setRecipeWithoutHistory(
      {
        ...latestRecipeRef.current,
        landmarks: initialLandmarks,
      },
      { notify: true },
    );
    landmarkFallbackRef.current = false;
  }, [imageUrl, initialLandmarks]);

  useEffect(() => {
    if (!imageUrl || !canShowLandmarkToggle || initialLandmarks || imageLoadVersion === 0) return;
    if (latestRecipeRef.current.landmarks && !landmarkFallbackRef.current) return;

    const image = baseImageRef.current;
    if (!image || !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0) return;

    const runId = ++landmarkDetectionRunRef.current;
    let cancelled = false;
    setIsDetectingLandmarks(true);

    void detectAnimeLandmarks(image)
      .then((detection) => {
        if (cancelled || runId !== landmarkDetectionRunRef.current) return;
        if (!detection) {
          ensureFallbackLandmarks({ notify: true });
          return;
        }
        if (latestRecipeRef.current.landmarks && !landmarkFallbackRef.current) return;

        setSecondaryLandmarks(detection.details);
        setLandmarkDebugInfo(detection.debug);
        landmarkFallbackRef.current = false;
        setRecipeWithoutHistory(
          {
            ...latestRecipeRef.current,
            landmarks: detection.controls,
          },
          { notify: true },
        );
      })
      .catch((error: unknown) => {
        if (!cancelled && runId === landmarkDetectionRunRef.current) {
          setLandmarkDebugInfo(null);
          setSecondaryLandmarks([]);
          ensureFallbackLandmarks({ notify: true });
        }
        console.warn("Anime landmark detection failed", error);
      })
      .finally(() => {
        if (!cancelled && runId === landmarkDetectionRunRef.current) {
          setIsDetectingLandmarks(false);
        }
      });

    return () => {
      cancelled = true;
      if (runId === landmarkDetectionRunRef.current) {
        setIsDetectingLandmarks(false);
      }
    };
  }, [canShowLandmarkToggle, imageLoadVersion, imageUrl, initialLandmarks]);

  function syncLiquifyHistoryAvailability() {
    setLiquifyHistoryAvailability({
      canRedo: redoStackRef.current.length > 0,
      canUndo: undoStackRef.current.length > 0,
    });
  }

  function syncAnnotationHistoryAvailability() {
    setAnnotationHistoryAvailability({
      canRedo: annotationRedoStackRef.current.length > 0,
      canUndo: annotationUndoStackRef.current.length > 0,
    });
  }

  function clearRecipeHistory() {
    undoStackRef.current = [];
    redoStackRef.current = [];
    activeHistoryKeyRef.current = null;
    annotationUndoStackRef.current = [];
    annotationRedoStackRef.current = [];
    activeAnnotationHistoryKeyRef.current = null;
    syncLiquifyHistoryAvailability();
    syncAnnotationHistoryAvailability();
  }

  function setRecipeWithoutHistory(nextRecipe: EditRecipe, options: { notify?: boolean } = {}) {
    latestRecipeRef.current = nextRecipe;
    shouldNotifyRecipeRef.current = Boolean(options.notify);
    setRecipe(nextRecipe);
  }

  function recordLiquifyHistory(previousLiquify: LiquifyStroke[], nextLiquify: LiquifyStroke[], historyKey?: string) {
    if (JSON.stringify(previousLiquify) === JSON.stringify(nextLiquify)) return;

    if (historyKey && activeHistoryKeyRef.current === historyKey && undoStackRef.current.length > 0) {
      const previousEntries = undoStackRef.current;
      const lastEntry = previousEntries[previousEntries.length - 1];
      undoStackRef.current = [...previousEntries.slice(0, -1), { ...lastEntry, after: nextLiquify }];
      redoStackRef.current = [];
      syncLiquifyHistoryAvailability();
      return;
    }

    undoStackRef.current = [...undoStackRef.current, { after: nextLiquify, before: previousLiquify, key: historyKey }].slice(-100);
    redoStackRef.current = [];
    activeHistoryKeyRef.current = historyKey ?? null;
    syncLiquifyHistoryAvailability();
  }

  function recordAnnotationHistory(
    previousAnnotations: AnnotationMark[],
    nextAnnotations: AnnotationMark[],
    historyKey?: string,
  ) {
    if (JSON.stringify(previousAnnotations) === JSON.stringify(nextAnnotations)) return;

    const before = previousAnnotations.map((annotation) => ({ ...annotation }));
    const after = nextAnnotations.map((annotation) => ({ ...annotation }));

    if (historyKey && activeAnnotationHistoryKeyRef.current === historyKey && annotationUndoStackRef.current.length > 0) {
      const previousEntries = annotationUndoStackRef.current;
      const lastEntry = previousEntries[previousEntries.length - 1];
      annotationUndoStackRef.current = [...previousEntries.slice(0, -1), { ...lastEntry, after }];
      annotationRedoStackRef.current = [];
      syncAnnotationHistoryAvailability();
      return;
    }

    annotationUndoStackRef.current = [...annotationUndoStackRef.current, { after, before, key: historyKey }].slice(-100);
    annotationRedoStackRef.current = [];
    activeAnnotationHistoryKeyRef.current = historyKey ?? null;
    syncAnnotationHistoryAvailability();
  }

  function updateRecipe(
    updater: (currentRecipe: EditRecipe) => EditRecipe,
    options: { history?: "annotation" | "liquify"; historyKey?: string } = {},
  ) {
    const currentRecipe = latestRecipeRef.current;
    const nextRecipe = normalizeEditRecipe(updater(currentRecipe));
    if (recipesMatch(nextRecipe, currentRecipe)) return;

    if (options.history === "liquify") {
      recordLiquifyHistory(currentRecipe.liquify, nextRecipe.liquify, options.historyKey);
      activeAnnotationHistoryKeyRef.current = null;
    } else if (options.history === "annotation") {
      recordAnnotationHistory(currentRecipe.annotations, nextRecipe.annotations, options.historyKey);
      activeHistoryKeyRef.current = null;
    } else {
      activeHistoryKeyRef.current = null;
      activeAnnotationHistoryKeyRef.current = null;
    }
    setRecipeWithoutHistory(nextRecipe, { notify: true });
  }

  function setRecipeWithHistory(nextRecipe: EditRecipe) {
    setRecipeWithoutHistory(normalizeEditRecipe(nextRecipe), { notify: true });
  }

  function ensureFallbackLandmarks(options: { notify?: boolean } = {}) {
    if (latestRecipeRef.current.landmarks && !landmarkFallbackRef.current) {
      setShowLandmarkControls(true);
      return;
    }

    landmarkFallbackRef.current = true;
    setShowLandmarkControls(true);
    setRecipeWithoutHistory(
      {
        ...latestRecipeRef.current,
        landmarks: createDefaultLandmarks(1, 1),
      },
      { notify: options.notify },
    );
  }

  function clearTransientEditState() {
    liquifyDrawingRef.current = false;
    lastLiquifyStrokePointRef.current = null;
    activeScaleEditIndexRef.current = null;
    activeLiquifyHistoryKeyRef.current = null;
    liquifyGestureStartRef.current = null;
    setPendingScaleStroke(null);
    setLiquifyBrushPreview(null);
    setIsScaleBrushDragging(false);
    currentScaleDragAmountRef.current = defaultLiquifyScaleAmount;
  }

  function handleUndo() {
    const historyEntry = undoStackRef.current.at(-1);
    if (!historyEntry) return;

    undoStackRef.current = undoStackRef.current.slice(0, -1);
    redoStackRef.current = [...redoStackRef.current, historyEntry].slice(-100);
    activeHistoryKeyRef.current = null;
    clearTransientEditState();
    setRecipeWithoutHistory({ ...latestRecipeRef.current, liquify: historyEntry.before }, { notify: true });
    syncLiquifyHistoryAvailability();
  }

  function handleRedo() {
    const historyEntry = redoStackRef.current.at(-1);
    if (!historyEntry) return;

    redoStackRef.current = redoStackRef.current.slice(0, -1);
    undoStackRef.current = [...undoStackRef.current, historyEntry].slice(-100);
    activeHistoryKeyRef.current = null;
    clearTransientEditState();
    setRecipeWithoutHistory({ ...latestRecipeRef.current, liquify: historyEntry.after }, { notify: true });
    syncLiquifyHistoryAvailability();
  }

  function handleAnnotationUndo() {
    const historyEntry = annotationUndoStackRef.current.at(-1);
    if (!historyEntry) return;

    annotationUndoStackRef.current = annotationUndoStackRef.current.slice(0, -1);
    annotationRedoStackRef.current = [...annotationRedoStackRef.current, historyEntry].slice(-100);
    activeAnnotationHistoryKeyRef.current = null;
    setSelectedAnnotationId((currentId) =>
      currentId && historyEntry.before.some((annotation) => annotation.id === currentId) ? currentId : null,
    );
    setRecipeWithoutHistory({ ...latestRecipeRef.current, annotations: historyEntry.before }, { notify: true });
    syncAnnotationHistoryAvailability();
  }

  function handleAnnotationRedo() {
    const historyEntry = annotationRedoStackRef.current.at(-1);
    if (!historyEntry) return;

    annotationRedoStackRef.current = annotationRedoStackRef.current.slice(0, -1);
    annotationUndoStackRef.current = [...annotationUndoStackRef.current, historyEntry].slice(-100);
    activeAnnotationHistoryKeyRef.current = null;
    setSelectedAnnotationId((currentId) =>
      currentId && historyEntry.after.some((annotation) => annotation.id === currentId) ? currentId : null,
    );
    setRecipeWithoutHistory({ ...latestRecipeRef.current, annotations: historyEntry.after }, { notify: true });
    syncAnnotationHistoryAvailability();
  }

  useEffect(() => {
    if (activeTool !== "annotation") return;

    function handleAnnotationShortcut(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      const tagName = target?.tagName.toLowerCase();
      if (target?.isContentEditable || tagName === "input" || tagName === "textarea" || tagName === "select") {
        return;
      }

      const key = event.key.toLowerCase();
      const isUndo = (event.ctrlKey || event.metaKey) && key === "z" && !event.shiftKey;
      const isRedo = (event.ctrlKey || event.metaKey) && (key === "y" || (key === "z" && event.shiftKey));
      if (!isUndo && !isRedo) return;

      event.preventDefault();
      if (isUndo) {
        handleAnnotationUndo();
      } else {
        handleAnnotationRedo();
      }
    }

    window.addEventListener("keydown", handleAnnotationShortcut);
    return () => window.removeEventListener("keydown", handleAnnotationShortcut);
  }, [activeTool]);

  function getStagePoint(event: PointerEvent<HTMLElement>) {
    const rect = imageViewportRef.current?.getBoundingClientRect() ?? stageRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return { x: 0.5, y: 0.5 };
    return {
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    };
  }

  function getStageMenuPosition(clientX: number, clientY: number) {
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return { x: 12, y: 12 };

    return {
      x: Math.min(Math.max(clientX - rect.left, 12), Math.max(12, rect.width - 188)),
      y: Math.min(Math.max(clientY - rect.top, 12), Math.max(12, rect.height - 96)),
    };
  }

  function getTrackedTouchPoints() {
    return [...activeStagePointersRef.current.values()].filter((point) => point.pointerType === "touch");
  }

  function getViewportGestureMetrics(points: StagePointerPoint[]) {
    if (points.length < 2) return null;
    const [first, second] = points;
    return {
      center: {
        x: (first.clientX + second.clientX) / 2,
        y: (first.clientY + second.clientY) / 2,
      },
      distance: Math.max(1, Math.hypot(second.clientX - first.clientX, second.clientY - first.clientY)),
    };
  }

  function setViewportTransform(nextZoom: number, nextPan: { x: number; y: number }) {
    const clampedZoom = clampEditorZoom(nextZoom);
    latestZoomRef.current = clampedZoom;
    latestPanRef.current = nextPan;
    setZoom(clampedZoom);
    setPan(nextPan);
  }

  function clearPendingTouchAction() {
    const pendingTouchAction = pendingTouchActionRef.current;
    if (!pendingTouchAction) return;
    window.clearTimeout(pendingTouchAction.longPressTimer);
    pendingTouchActionRef.current = null;
  }

  function clearActivePointerEditState() {
    annotationDragRef.current = null;
    annotationCreateRef.current = null;
    annotationResizeRef.current = null;
    activeAnnotationHistoryKeyRef.current = null;
    landmarkDragRef.current = null;
    localMaskActiveStrokeIdRef.current = null;
    panDragRef.current = null;
    clearTransientEditState();
  }

  function openSaveMenuAt(clientX: number, clientY: number) {
    clearActivePointerEditState();
    setSaveMenuPosition(getStageMenuPosition(clientX, clientY));
  }

  function beginViewportGesture() {
    const metrics = getViewportGestureMetrics(getTrackedTouchPoints());
    if (!metrics) return false;
    clearPendingTouchAction();
    clearActivePointerEditState();
    setSaveMenuPosition(null);
    viewportGestureRef.current = {
      startCenter: metrics.center,
      startDistance: metrics.distance,
      startPan: latestPanRef.current,
      startZoom: latestZoomRef.current,
    };
    return true;
  }

  function updateViewportGesture() {
    const gesture = viewportGestureRef.current;
    const metrics = getViewportGestureMetrics(getTrackedTouchPoints());
    const stageRect = stageRef.current?.getBoundingClientRect();
    if (!gesture || !metrics || !stageRect) return false;

    const nextZoom = clampEditorZoom(gesture.startZoom * (metrics.distance / gesture.startDistance));
    const zoomRatio = nextZoom / gesture.startZoom;
    const stageCenter = {
      x: stageRect.left + stageRect.width / 2,
      y: stageRect.top + stageRect.height / 2,
    };
    const nextPan = {
      x:
        metrics.center.x -
        stageCenter.x -
        zoomRatio * (gesture.startCenter.x - stageCenter.x - gesture.startPan.x),
      y:
        metrics.center.y -
        stageCenter.y -
        zoomRatio * (gesture.startCenter.y - stageCenter.y - gesture.startPan.y),
    };
    setViewportTransform(nextZoom, nextPan);
    return true;
  }

  function trackStagePointer(event: PointerEvent<HTMLElement>) {
    activeStagePointersRef.current.set(event.pointerId, {
      clientX: event.clientX,
      clientY: event.clientY,
      pointerType: event.pointerType,
    });
  }

  function updateTrackedStagePointer(event: PointerEvent<HTMLElement>) {
    const currentPointer = activeStagePointersRef.current.get(event.pointerId);
    if (!currentPointer) return;
    activeStagePointersRef.current.set(event.pointerId, {
      ...currentPointer,
      clientX: event.clientX,
      clientY: event.clientY,
    });
  }

  function untrackStagePointer(pointerId: number) {
    activeStagePointersRef.current.delete(pointerId);
    if (getTrackedTouchPoints().length < 2) {
      viewportGestureRef.current = null;
    }
  }

  function hasTouchMoved(pendingTouchAction: PendingTouchAction, event: PointerEvent<HTMLElement>) {
    return Math.hypot(event.clientX - pendingTouchAction.clientX, event.clientY - pendingTouchAction.clientY) > touchMoveTolerance;
  }

  function clampAnnotationPoint(point: { x: number; y: number }) {
    return {
      x: Math.min(1, Math.max(0, Number(point.x.toFixed(4)))),
      y: Math.min(1, Math.max(0, Number(point.y.toFixed(4)))),
    };
  }

  function beginLocalMaskStroke(point: { x: number; y: number }) {
    const strokeId = `local-mask-${Date.now()}-${localMaskStrokes.length}`;
    localMaskActiveStrokeIdRef.current = strokeId;
    setLocalMaskBrushPreview({ radius: localMaskRadius, x: point.x, y: point.y });
    setLocalMaskStrokes((current) => [
      ...current,
      {
        id: strokeId,
        mode: localMaskMode,
        points: [clampAnnotationPoint(point)],
        radius: localMaskRadius,
      },
    ]);
  }

  function appendLocalMaskPoint(point: { x: number; y: number }) {
    const strokeId = localMaskActiveStrokeIdRef.current;
    setLocalMaskBrushPreview({ radius: localMaskRadius, x: point.x, y: point.y });
    if (!strokeId) return;
    const nextPoint = clampAnnotationPoint(point);
    setLocalMaskStrokes((current) =>
      current.map((stroke) =>
        stroke.id === strokeId
          ? {
              ...stroke,
              points: [...stroke.points, nextPoint],
            }
          : stroke,
      ),
    );
  }

  function buildRectFromDrag(start: { x: number; y: number }, end: { x: number; y: number }) {
    const startPoint = clampAnnotationPoint(start);
    const endPoint = clampAnnotationPoint(end);
    return {
      endX: endPoint.x,
      endY: endPoint.y,
      height: Math.max(0.01, Math.abs(endPoint.y - startPoint.y)),
      width: Math.max(0.01, Math.abs(endPoint.x - startPoint.x)),
      x: Math.min(startPoint.x, endPoint.x),
      y: Math.min(startPoint.y, endPoint.y),
    };
  }

  function resizeAnnotationFromHandle(
    annotation: AnnotationMark,
    handle: AnnotationResizeHandle,
    point: { x: number; y: number },
  ): Partial<AnnotationMark> {
    const nextPoint = clampAnnotationPoint(point);
    if (handle === "end") {
      return { endX: nextPoint.x, endY: nextPoint.y };
    }

    const left = Math.min(annotation.x, annotation.endX);
    const right = Math.max(annotation.x, annotation.endX);
    const top = Math.min(annotation.y, annotation.endY);
    const bottom = Math.max(annotation.y, annotation.endY);
    const fixedX = handle.includes("w") ? right : left;
    const fixedY = handle.includes("n") ? bottom : top;
    return buildRectFromDrag({ x: fixedX, y: fixedY }, nextPoint);
  }

  function startStageToolAction(point: { x: number; y: number }, pointerId?: number) {
    lastStagePointRef.current = point;

    if (activeTool === "local-generate") {
      beginLocalMaskStroke(point);
      return;
    }

    if (activeTool === "annotation") {
      if (annotationToolMode === "arrow" || annotationToolMode === "rect") {
        const annotationId = `annotation-${latestRecipeRef.current.annotations.length + 1}`;
        if (pointerId !== undefined) {
          annotationCreateRef.current = {
            annotationId,
            kind: annotationToolMode,
            pointerId,
            start: point,
          };
        }
        updateRecipe(
          (currentRecipe) =>
            addAnnotationMark(currentRecipe, {
              color: annotationColor,
              endX: point.x,
              endY: point.y,
              height: 0.01,
              kind: annotationToolMode,
              strokeWidth: 4,
              width: 0.01,
              x: point.x,
              y: point.y,
            }),
          { history: "annotation", historyKey: `annotation-create:${annotationId}` },
        );
        setSelectedAnnotationId(annotationId);
        return;
      }
      const kind = annotationToolMode === "pin" ? "callout" : annotationToolMode;
      const annotationId = `annotation-${latestRecipeRef.current.annotations.length + 1}`;
      updateRecipe(
        (currentRecipe) =>
          addAnnotationMark(currentRecipe, {
            color: annotationColor,
            fontSize: annotationFontSize,
            kind,
            text: kind === "text" ? "文字" : "",
            x: point.x,
            y: point.y,
          }),
        { history: "annotation", historyKey: `annotation-add:${annotationId}` },
      );
      setSelectedAnnotationId(annotationId);
      return;
    }

    if (activeTool !== "liquify") return;

    if (liquifyToolMode === "scale") {
      liquifyOperationIdRef.current += 1;
      setScaleBrushPoint(point);
      setIsScaleBrushDragging(true);
      setLiquifyScaleAmount(defaultLiquifyScaleAmount);
      return;
    }

    liquifyDrawingRef.current = true;
    activeLiquifyHistoryKeyRef.current = `warp:${++liquifyOperationIdRef.current}`;
    lastLiquifyStrokePointRef.current = point;
    setLiquifyBrushPreview({ active: true, mode: "warp", radius: brushRadius, x: point.x, y: point.y });
  }

  function handleToolChange(tool: EditorTool) {
    setActiveTool(tool);
    setSelectedAnnotationId(null);
    setSelectedLandmarkKey(null);
    setTemporarilyHideLandmarks(false);
    if (tool === "face" || tool === "eyes") {
      ensureFallbackLandmarks({ notify: true });
    }
    if (tool !== "liquify") {
      clearTransientEditState();
    }
  }

  function resetAllAdjustments() {
    clearRecipeHistory();
    setRecipeWithoutHistory({
      ...createEmptyRecipe(),
      annotations: latestRecipeRef.current.annotations,
      landmarks: latestRecipeRef.current.landmarks,
    }, { notify: true });
  }

  function handleZoomChange(value: number) {
    setViewportTransform(value, latestPanRef.current);
  }

  function handleBaseImageLoad(event: SyntheticEvent<HTMLImageElement>) {
    const image = event.currentTarget;
    const loadedUrl = image.currentSrc || image.src;
    const expectedUrl = latestImageUrlRef.current;

    if (expectedUrl && loadedUrl) {
      try {
        const loadedHref = new URL(loadedUrl, window.location.href).href;
        const expectedHref = new URL(expectedUrl, window.location.href).href;
        if (loadedHref !== expectedHref) return;
      } catch {
        if (loadedUrl !== expectedUrl) return;
      }
    }

    const nextWidth = image.naturalWidth || image.width || stageWidth;
    const nextHeight = image.naturalHeight || image.height || stageHeight;
    setDetectedImageSize({ height: nextHeight, width: nextWidth });
    setImageLoadVersion((current) => current + 1);
  }

  function clearExtraReference() {
    if (extraReferencePreviewUrl) URL.revokeObjectURL(extraReferencePreviewUrl);
    setExtraReferenceFile(null);
    setExtraReferencePreviewUrl(null);
    setExtraReferenceDescription("");
    if (extraReferenceInputRef.current) extraReferenceInputRef.current.value = "";
  }

  function handleExtraReferenceSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    if (extraReferencePreviewUrl) URL.revokeObjectURL(extraReferencePreviewUrl);
    setExtraReferenceFile(file);
    setExtraReferencePreviewUrl(URL.createObjectURL(file));
  }

  function handleLocalReferenceSelected(event: ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    setLocalUploadedReferenceFile(file);
  }

  function toggleLocalReferenceKey(referenceKey: string) {
    setLocalUploadedReferenceFile(null);
    setLocalUploadedReferenceDescription("");
    if (localReferenceInputRef.current) {
      localReferenceInputRef.current.value = "";
    }
    setSelectedLocalReferenceKeys((current) => (current.includes(referenceKey) ? [] : [referenceKey]));
  }

  async function exportEditedBlob(includeAnnotations: boolean, annotations = latestRecipeRef.current.annotations) {
    const pixiBlob = await pixiStageRef.current?.exportImage();
    if (pixiBlob) {
      if (!includeAnnotations) return pixiBlob;
      const image = await loadBlobImage(pixiBlob);
      const outputCanvas = document.createElement("canvas");
      outputCanvas.width = image.naturalWidth || image.width || stageWidth;
      outputCanvas.height = image.naturalHeight || image.height || stageHeight;
      const context = outputCanvas.getContext("2d");
      if (!context) throw new Error("Unable to export editor image");
      context.drawImage(image, 0, 0, outputCanvas.width, outputCanvas.height);
      drawAnnotationsToCanvas(context, annotations, outputCanvas.width, outputCanvas.height);
      return canvasToPngBlob(outputCanvas);
    }

    const image = baseImageRef.current;
    if (!image) throw new Error("No editor image is loaded");
    const outputCanvas = document.createElement("canvas");
    outputCanvas.width = image.naturalWidth || stageWidth;
    outputCanvas.height = image.naturalHeight || stageHeight;
    const context = outputCanvas.getContext("2d");
    if (!context) throw new Error("Unable to export editor image");
    context.drawImage(image, 0, 0, outputCanvas.width, outputCanvas.height);
    if (includeAnnotations) {
      drawAnnotationsToCanvas(context, annotations, outputCanvas.width, outputCanvas.height);
    }
    return canvasToPngBlob(outputCanvas);
  }

  async function handleSave() {
    const compactRecipe = compactRecipeAnnotations(latestRecipeRef.current);
    const imageBlob = await exportEditedBlob(false);
    const fileName = `kigcraft-edit-${Date.now()}.png`;
    downloadBlob(imageBlob, fileName);
    await onSave?.({ annotationPrompt: buildAnnotationPrompt(compactRecipe.annotations), fileName, imageBlob, recipe: compactRecipe });
  }

  async function handleRegenerate() {
    if (!onRegenerate) return;
    const compactRecipe = compactRecipeAnnotations(latestRecipeRef.current);
    const editedImageBlob = await exportEditedBlob(false);
    const annotatedImageBlob =
      compactRecipe.annotations.length > 0 ? await exportEditedBlob(true, compactRecipe.annotations) : undefined;
    const promptNote = extraReferenceDescription.trim();
    await onRegenerate({
      annotationPrompt: buildAnnotationPrompt(compactRecipe.annotations),
      annotatedImageBlob,
      editedImageBlob,
      promptNote: promptNote || undefined,
      extraReference: extraReferenceFile ? { description: extraReferenceDescription.trim(), file: extraReferenceFile } : undefined,
      recipe: compactRecipe,
    });
  }

  async function handleSecondaryRegenerate() {
    if (!onSecondaryRegenerate) return;
    const compactRecipe = compactRecipeAnnotations(latestRecipeRef.current);
    const editedImageBlob = await exportEditedBlob(false);
    const annotatedImageBlob =
      compactRecipe.annotations.length > 0 ? await exportEditedBlob(true, compactRecipe.annotations) : undefined;
    const promptNote = extraReferenceDescription.trim();
    await onSecondaryRegenerate({
      annotationPrompt: buildAnnotationPrompt(compactRecipe.annotations),
      annotatedImageBlob,
      editedImageBlob,
      promptNote: promptNote || undefined,
      extraReference: extraReferenceFile ? { description: extraReferenceDescription.trim(), file: extraReferenceFile } : undefined,
      recipe: compactRecipe,
    });
  }

  async function handleLocalGenerate() {
    if (!onLocalGenerate || !hasLocalMaskPaint(localMaskStrokes)) return;
    setLocalGenerateError(null);
    try {
      const compactRecipe = compactRecipeAnnotations(latestRecipeRef.current);
      const baseImageBlob = await exportEditedBlob(false);
      const baseImage = await loadBlobImage(baseImageBlob);
      const maskImageBlob = await exportLocalMaskBlob(
        localMaskStrokes,
        baseImage.naturalWidth || baseImage.width || stageWidth,
        baseImage.naturalHeight || baseImage.height || stageHeight,
      );
      await onLocalGenerate({
        baseImageBlob,
        editNote: localEditNote.trim(),
        maskImageBlob,
        recipe: compactRecipe,
        selectedReferenceKeys: [],
        uploadedReferences: localUploadedReferenceFile
          ? [{ description: localEditNote.trim(), file: localUploadedReferenceFile }]
          : [],
      });
    } catch (error) {
      setLocalGenerateError(error instanceof Error ? error.message : "局部生成提交失败。");
    }
  }

  useImperativeHandle(
    ref,
    () => ({
      regenerate: handleRegenerate,
      secondaryRegenerate: handleSecondaryRegenerate,
    }),
    [handleRegenerate, handleSecondaryRegenerate],
  );

  function beginParameterInteraction(key?: EyeControlKey) {
    if (key !== "eyeRegionScale") {
      setTemporarilyHideLandmarks(true);
    }
  }

  function endParameterInteraction() {
    setTemporarilyHideLandmarks(false);
  }

  function handleFaceControlChange(key: keyof EditRecipe["face"], value: number) {
    updateRecipe((currentRecipe) => updateFaceControl(currentRecipe, key, value));
  }

  function handleFaceControlReset(key: keyof EditRecipe["face"]) {
    updateRecipe((currentRecipe) => updateFaceControl(currentRecipe, key, createEmptyRecipe().face[key]));
  }

  function handleEyeControlChange(key: EyeControlKey, value: number) {
    updateRecipe((currentRecipe) => updateEyeControl(currentRecipe, key, value));
  }

  function handleEyeControlReset(key: EyeControlKey) {
    updateRecipe((currentRecipe) => updateEyeControl(currentRecipe, key, defaultEyeControlValues[key]));
  }

  function handleMouthControlChange(key: MouthControlKey, value: number) {
    updateRecipe((currentRecipe) => updateMouthControl(currentRecipe, key, value));
  }

  function handleMouthControlReset(key: MouthControlKey) {
    updateRecipe((currentRecipe) => updateMouthControl(currentRecipe, key, createEmptyRecipe().mouth[key]));
  }

  function handleLiquifyBrushRadiusChange(value: number) {
    setBrushRadius(value);
  }

  function handleLiquifyBrushRadiusReset() {
    setBrushRadius(defaultBrushRadius);
  }

  function handleLiquifyWarpStrengthChange(value: number) {
    setLiquifyWarpStrength(value);
  }

  function handleLiquifyWarpStrengthReset() {
    setLiquifyWarpStrength(defaultLiquifyWarpStrength);
  }

  function handleLiquifyScaleAmountChange(value: number) {
    setLiquifyScaleAmount(value);
    const point = scaleBrushPoint ?? lastStagePointRef.current;
    if (!point || value === 0) return;
    const stroke = createLiquifyStrokeFromNormalizedPoint({
      mode: "scale",
      radius: brushRadius,
      scale: value / 10,
      strength: Math.abs(value / 10),
      x: point.x,
      y: point.y,
    });
    updateRecipe((currentRecipe) => updateLiquifyBrush(currentRecipe, stroke), {
      history: "liquify",
      historyKey: `scale:${liquifyOperationIdRef.current}`,
    });
  }

  function handleLiquifyScaleAmountReset() {
    setLiquifyScaleAmount(defaultLiquifyScaleAmount);
    currentScaleDragAmountRef.current = defaultLiquifyScaleAmount;
  }

  function handleStagePointerDown(event: PointerEvent<HTMLElement>) {
    if (!imageUrl) return;
    setShowLandmarkHint(false);
    const point = getStagePoint(event);
    event.currentTarget.setPointerCapture?.(event.pointerId);
    setSaveMenuPosition(null);

    if (event.button === 2 && event.pointerType === "mouse") {
      event.preventDefault();
      clearPendingTouchAction();
      clearActivePointerEditState();
      panDragRef.current = {
        moved: false,
        pointerId: event.pointerId,
        startClientX: event.clientX,
        startClientY: event.clientY,
        startPanX: latestPanRef.current.x,
        startPanY: latestPanRef.current.y,
      };
      return;
    }

    trackStagePointer(event);

    if (event.pointerType === "touch") {
      if (getTrackedTouchPoints().length >= 2) {
        beginViewportGesture();
        return;
      }

      clearPendingTouchAction();
      const pendingTouchAction: PendingTouchAction = {
        clientX: event.clientX,
        clientY: event.clientY,
        longPressTimer: 0,
        menuOpened: false,
        moved: false,
        point,
        pointerId: event.pointerId,
        startedEdit: false,
      };
      pendingTouchAction.longPressTimer = window.setTimeout(() => {
        const currentPendingTouchAction = pendingTouchActionRef.current;
        if (!currentPendingTouchAction || currentPendingTouchAction.pointerId !== event.pointerId) return;
        currentPendingTouchAction.menuOpened = true;
        openSaveMenuAt(currentPendingTouchAction.clientX, currentPendingTouchAction.clientY);
      }, touchLongPressMs);
      pendingTouchActionRef.current = pendingTouchAction;
      return;
    }

    startStageToolAction(point, event.pointerId);
  }

  function handleStagePointerMove(event: PointerEvent<HTMLElement>) {
    updateTrackedStagePointer(event);

    const panDrag = panDragRef.current;
    if (panDrag?.pointerId === event.pointerId) {
      event.preventDefault();
      const deltaX = event.clientX - panDrag.startClientX;
      const deltaY = event.clientY - panDrag.startClientY;
      if (Math.hypot(deltaX, deltaY) > 3) {
        panDrag.moved = true;
      }
      setViewportTransform(latestZoomRef.current, {
        x: panDrag.startPanX + deltaX,
        y: panDrag.startPanY + deltaY,
      });
      return;
    }

    if (viewportGestureRef.current) {
      event.preventDefault();
      updateViewportGesture();
      return;
    }

    const point = getStagePoint(event);
    lastStagePointRef.current = point;

    const pendingTouchAction = pendingTouchActionRef.current;
    if (pendingTouchAction?.pointerId === event.pointerId) {
      if (hasTouchMoved(pendingTouchAction, event)) {
        window.clearTimeout(pendingTouchAction.longPressTimer);
        pendingTouchAction.moved = true;
      }
      if (pendingTouchAction.menuOpened) {
        return;
      }
      if (
        pendingTouchAction.moved &&
        (activeTool === "liquify" || activeTool === "local-generate") &&
        !pendingTouchAction.startedEdit
      ) {
        pendingTouchAction.startedEdit = true;
        startStageToolAction(pendingTouchAction.point, pendingTouchAction.pointerId);
      }
      if (!pendingTouchAction.startedEdit) {
        return;
      }
    }

    if (activeTool === "local-generate") {
      appendLocalMaskPoint(point);
      return;
    }

    if (landmarkDragRef.current) {
      updateRecipe((currentRecipe) => updateManualLandmark(currentRecipe, landmarkDragRef.current!.key, point));
      return;
    }

    if (annotationCreateRef.current?.pointerId === event.pointerId) {
      const create = annotationCreateRef.current;
      const patch = create.kind === "arrow" ? { endX: point.x, endY: point.y } : buildRectFromDrag(create.start, point);
      updateRecipe((currentRecipe) => updateAnnotationMark(currentRecipe, create.annotationId, patch), {
        history: "annotation",
        historyKey: `annotation-create:${create.annotationId}`,
      });
      return;
    }

    if (annotationResizeRef.current?.pointerId === event.pointerId) {
      const resize = annotationResizeRef.current;
      const patch = resizeAnnotationFromHandle(resize.original, resize.handle, point);
      updateRecipe((currentRecipe) => updateAnnotationMark(currentRecipe, resize.annotationId, patch), {
        history: "annotation",
        historyKey: `annotation-resize:${resize.annotationId}`,
      });
      return;
    }

    if (annotationDragRef.current) {
      const drag = annotationDragRef.current;
      updateRecipe(
        (currentRecipe) =>
          moveAnnotationMark(currentRecipe, drag.annotationId, {
            x: point.x - drag.offsetX,
            y: point.y - drag.offsetY,
          }),
        { history: "annotation", historyKey: `annotation-move:${drag.annotationId}` },
      );
      return;
    }

    if (activeTool !== "liquify") return;

    if (liquifyToolMode === "scale" && isScaleBrushDragging) {
      setScaleBrushPoint(point);
      return;
    }

    if (liquifyDrawingRef.current && lastLiquifyStrokePointRef.current) {
      const from = lastLiquifyStrokePointRef.current;
      const stroke = createLiquifyWarpStrokeFromDrag({ from, radius: brushRadius, strength: liquifyWarpStrength, to: point });
      if (stroke.strength > 0) {
        updateRecipe((currentRecipe) => updateLiquifyBrush(currentRecipe, stroke), {
          history: "liquify",
          historyKey: activeLiquifyHistoryKeyRef.current ?? undefined,
        });
      }
      lastLiquifyStrokePointRef.current = point;
      setLiquifyBrushPreview({ active: true, mode: "warp", radius: brushRadius, x: point.x, y: point.y });
      return;
    }

    setLiquifyBrushPreview({ active: false, mode: liquifyToolMode, radius: brushRadius, x: point.x, y: point.y });
  }

  function handleStagePointerUp(event: PointerEvent<HTMLElement>) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    const panDrag = panDragRef.current;
    if (panDrag?.pointerId === event.pointerId) {
      event.preventDefault();
      suppressNextContextMenuRef.current = panDrag.moved;
      panDragRef.current = null;
      return;
    }

    const hadViewportGesture = Boolean(viewportGestureRef.current);
    untrackStagePointer(event.pointerId);

    const pendingTouchAction = pendingTouchActionRef.current;
    if (pendingTouchAction?.pointerId === event.pointerId) {
      window.clearTimeout(pendingTouchAction.longPressTimer);
      if (!hadViewportGesture && !pendingTouchAction.menuOpened && !pendingTouchAction.moved && !pendingTouchAction.startedEdit) {
        startStageToolAction(getStagePoint(event), event.pointerId);
      }
      pendingTouchActionRef.current = null;
    }

    liquifyDrawingRef.current = false;
    lastLiquifyStrokePointRef.current = null;
    activeLiquifyHistoryKeyRef.current = null;
    activeAnnotationHistoryKeyRef.current = null;
    annotationCreateRef.current = null;
    annotationResizeRef.current = null;
    annotationDragRef.current = null;
    landmarkDragRef.current = null;
    localMaskActiveStrokeIdRef.current = null;
    setIsScaleBrushDragging(false);
    setLiquifyBrushPreview((current) => (current ? { ...current, active: false } : current));
  }

  function handleStagePointerLeave() {
    if (activeTool === "local-generate") {
      setLocalMaskBrushPreview(null);
      localMaskActiveStrokeIdRef.current = null;
    }
    if (activeTool !== "liquify" || liquifyToolMode !== "scale") {
      setLiquifyBrushPreview(null);
    }
    liquifyDrawingRef.current = false;
    lastLiquifyStrokePointRef.current = null;
    setIsScaleBrushDragging(false);
  }

  function handleStagePointerCancel(event: PointerEvent<HTMLElement>) {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    untrackStagePointer(event.pointerId);
    clearPendingTouchAction();
    clearActivePointerEditState();
  }

  function handleStageContextMenu(event: MouseEvent<HTMLElement>) {
    if (!imageUrl) return;
    event.preventDefault();
    if (suppressNextContextMenuRef.current) {
      suppressNextContextMenuRef.current = false;
      return;
    }
    openSaveMenuAt(event.clientX, event.clientY);
  }

  function handleAnnotationPointerDown(annotationId: string, event: PointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    setShowLandmarkHint(false);
    const point = getStagePoint(event);
    const annotation = latestRecipeRef.current.annotations.find((mark) => mark.id === annotationId);
    if (annotation?.color) setAnnotationColor(annotation.color);
    if (annotation?.fontSize) setAnnotationFontSize(annotation.fontSize);
    annotationDragRef.current = {
      annotationId,
      offsetX: annotation ? point.x - annotation.x : 0,
      offsetY: annotation ? point.y - annotation.y : 0,
      pointerId: event.pointerId,
    };
    setSelectedAnnotationId(annotationId);
    lastStagePointRef.current = point;
  }

  function handleAnnotationHandlePointerDown(annotationId: string, handle: string, event: PointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    setShowLandmarkHint(false);
    const annotation = latestRecipeRef.current.annotations.find((mark) => mark.id === annotationId);
    if (!annotation) return;

    setSelectedAnnotationId(annotationId);
    annotationResizeRef.current = {
      annotationId,
      handle: handle as AnnotationResizeHandle,
      original: { ...annotation },
      pointerId: event.pointerId,
      start: getStagePoint(event),
    };
  }

  function handleAnnotationDelete(annotationId: string, event: PointerEvent<HTMLButtonElement>) {
    event.stopPropagation();
    updateRecipe((currentRecipe) => removeAnnotationMark(currentRecipe, annotationId), {
      history: "annotation",
      historyKey: `annotation-delete:${annotationId}`,
    });
    setSelectedAnnotationId(null);
  }

  function handleLandmarkPointerDown(key: ManualLandmarkKey, event: PointerEvent<HTMLDivElement>) {
    event.stopPropagation();
    setShowLandmarkHint(false);
    landmarkFallbackRef.current = false;
    landmarkDragRef.current = { key, offsetX: 0, offsetY: 0, pointerId: event.pointerId };
    setSelectedLandmarkKey(key);
  }

  async function recognizeFaceLandmarks() {
    if (!imageUrl || !canShowLandmarkToggle) return;

    const image = baseImageRef.current;
    if (!image || !image.complete || image.naturalWidth <= 0 || image.naturalHeight <= 0) return;

    const runId = ++landmarkDetectionRunRef.current;
    setIsDetectingLandmarks(true);
    try {
      const detection = await detectAnimeLandmarks(image);
      if (runId !== landmarkDetectionRunRef.current) return;
      if (!detection) {
        setLandmarkDebugInfo(null);
        setSecondaryLandmarks([]);
        ensureFallbackLandmarks({ notify: true });
        return;
      }

      setSecondaryLandmarks(detection.details);
      setLandmarkDebugInfo(detection.debug);
      landmarkFallbackRef.current = false;
      setRecipeWithoutHistory(
        {
          ...latestRecipeRef.current,
          landmarks: detection.controls,
        },
        { notify: true },
      );
      setShowLandmarkControls(true);
    } catch (error: unknown) {
      if (runId === landmarkDetectionRunRef.current) {
        setLandmarkDebugInfo(null);
        setSecondaryLandmarks([]);
        ensureFallbackLandmarks({ notify: true });
      }
      console.warn("Anime landmark detection failed", error);
    } finally {
      if (runId === landmarkDetectionRunRef.current) {
        setIsDetectingLandmarks(false);
      }
    }
  }

  function renderToolControls() {
    if (activeTool === "annotation") {
      const selectedAnnotationIndex = recipe.annotations.findIndex((annotation) => annotation.id === selectedAnnotationId);
      const selectedAnnotation = selectedAnnotationIndex >= 0 ? recipe.annotations[selectedAnnotationIndex] : null;
      const annotationModes: Array<{ icon: typeof IconCircleDot; label: string; value: AnnotationToolMode }> = [
        { icon: IconCircleDot, label: "标注", value: "pin" },
        { icon: IconArrowUpRight, label: "箭头", value: "arrow" },
        { icon: IconRectangle, label: "矩形", value: "rect" },
        { icon: IconTypography, label: "文字", value: "text" },
      ];
      const annotationColors = ["#ef4444", "#f97316", "#facc15", "#22c55e", "#38bdf8", "#a855f7", "#ffffff"];

      return (
        <Stack gap={1.25} data-testid="annotation-controls">
          <Group gap={0.75} wrap="wrap">
            {annotationModes.map((mode) => {
              const Icon = mode.icon;
              const selected = annotationToolMode === mode.value;
              return (
                <Tooltip key={mode.value} label={mode.label}>
                  <ActionIcon
                    aria-label={mode.label}
                    color={selected ? "cyan" : "gray"}
                    data-active={selected ? "true" : "false"}
                    data-testid={`annotation-mode-${mode.value}`}
                    onClick={() => setAnnotationToolMode(mode.value)}
                    size="lg"
                    style={{
                      background: selected ? "var(--kb-dirty-yellow)" : "var(--kb-panel-soft)",
                      border: "2px solid var(--kb-line)",
                      borderRadius: 0,
                      boxShadow: selected ? "var(--kb-hard-shadow-sm)" : "none",
                      color: selected ? "var(--kb-off-white)" : "var(--kb-ink)",
                    }}
                    variant={selected ? "filled" : "light"}
                  >
                    <Icon color={selected ? "var(--kb-off-white)" : "var(--kb-ink)"} size={18} stroke={2.25} />
                  </ActionIcon>
                </Tooltip>
              );
            })}
          </Group>

          <Group gap={0.75} wrap="wrap">
            <Tooltip label="撤销 Ctrl+Z">
              <ActionIcon
                aria-label="撤销"
                color={canUndoAnnotation ? "cyan" : "gray"}
                data-active={canUndoAnnotation ? "true" : "false"}
                data-testid="annotation-undo"
                disabled={!canUndoAnnotation}
                onClick={handleAnnotationUndo}
                size="lg"
                style={{
                  background: "var(--kb-panel-soft)",
                  border: "2px solid var(--kb-line)",
                  borderRadius: 0,
                  color: canUndoAnnotation ? "var(--kb-dirty-yellow)" : "var(--kb-concrete-grey)",
                }}
                variant="light"
              >
                <IconArrowBackUp size={18} />
              </ActionIcon>
            </Tooltip>
            <Tooltip label="重做 Ctrl+Y / Ctrl+Shift+Z">
              <ActionIcon
                aria-label="重做"
                color={canRedoAnnotation ? "cyan" : "gray"}
                data-active={canRedoAnnotation ? "true" : "false"}
                data-testid="annotation-redo"
                disabled={!canRedoAnnotation}
                onClick={handleAnnotationRedo}
                size="lg"
                style={{
                  background: "var(--kb-panel-soft)",
                  border: "2px solid var(--kb-line)",
                  borderRadius: 0,
                  color: canRedoAnnotation ? "var(--kb-dirty-yellow)" : "var(--kb-concrete-grey)",
                }}
                variant="light"
              >
                <IconArrowForwardUp size={18} />
              </ActionIcon>
            </Tooltip>
          </Group>

          <Group gap={0.75} wrap="wrap">
            {annotationColors.map((color) => (
              <Tooltip key={color} label={`颜色 ${color}`}>
                <button
                  aria-label={`标注颜色 ${color}`}
                  data-testid={`annotation-color-${color}`}
                  onClick={() => {
                    setAnnotationColor(color);
                    if (selectedAnnotation) {
                      updateRecipe(
                        (currentRecipe) => updateAnnotationMark(currentRecipe, selectedAnnotation.id, { color }),
                        { history: "annotation", historyKey: `annotation-color:${selectedAnnotation.id}` },
                      );
                    }
                  }}
                  style={{
                    background: color,
                    border: annotationColor === color ? "3px solid var(--kb-ink)" : "2px solid var(--kb-line)",
                    borderRadius: 0,
                    boxShadow: annotationColor === color ? "var(--kb-hard-shadow-sm)" : "none",
                    cursor: "pointer",
                    height: 24,
                    padding: 0,
                    width: 24,
                  }}
                  type="button"
                />
              </Tooltip>
            ))}
          </Group>

          {selectedAnnotation ? (
            <Stack
              gap={1}
              p={1}
              style={{
                background: "var(--kb-panel-soft)",
                border: "2px solid var(--kb-line)",
                borderRadius: 0,
                boxShadow: "var(--kb-hard-shadow-sm)",
              }}
            >
              <Text c="white" fw={800} size="sm">
                标注 {selectedAnnotationIndex + 1}
              </Text>

              {selectedAnnotation.kind === "text" ? (
                <>
                  <TextInput
                    data-testid={`annotation-text-input-${selectedAnnotation.id}`}
                    label="文字"
                    onChange={(event: ChangeEvent<HTMLInputElement>) =>
                      updateRecipe(
                        (currentRecipe) =>
                          updateAnnotationMark(currentRecipe, selectedAnnotation.id, { text: event.currentTarget.value }),
                        { history: "annotation", historyKey: `annotation-text:${selectedAnnotation.id}` },
                      )
                    }
                    value={selectedAnnotation.text ?? ""}
                  />
                  <Group align="center" gap={1} wrap="nowrap">
                    <Text c="dimmed" miw={42} size="sm">
                      字号
                    </Text>
                    <Slider
                      data-testid={`annotation-font-size-${selectedAnnotation.id}`}
                      max={72}
                      min={12}
                      onChange={(value: number) => {
                        setAnnotationFontSize(value);
                        updateRecipe(
                          (currentRecipe) => updateAnnotationMark(currentRecipe, selectedAnnotation.id, { fontSize: value }),
                          { history: "annotation", historyKey: `annotation-font:${selectedAnnotation.id}` },
                        );
                      }}
                      step={1}
                      style={{ flex: 1 }}
                      value={selectedAnnotation.fontSize ?? annotationFontSize}
                    />
                    <Text c="dimmed" miw={32} size="sm" ta="right">
                      {selectedAnnotation.fontSize ?? annotationFontSize}
                    </Text>
                  </Group>
                </>
              ) : selectedAnnotation.kind === "pin" || selectedAnnotation.kind === "callout" ? (
                <Textarea
                  data-testid={`annotation-note-input-${selectedAnnotation.id}`}
                  minRows={3}
                  onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
                    updateRecipe(
                      (currentRecipe) =>
                        updateAnnotationNote(currentRecipe, selectedAnnotation.id, event.currentTarget.value),
                      { history: "annotation", historyKey: `annotation-note:${selectedAnnotation.id}` },
                    )
                  }
                  placeholder="输入说明"
                  value={selectedAnnotation.note}
                />
              ) : null}
            </Stack>
          ) : null}
        </Stack>
      );

      return (
        <Stack gap={1.25} data-testid="annotation-controls">
          {recipe.annotations.length > 0 ? (
            <Group gap={0.75} wrap="wrap">
              {recipe.annotations.map((annotation, index) => (
                <Button
                  data-testid={`annotation-select-${annotation.id}`}
                  key={annotation.id}
                  onClick={() => setSelectedAnnotationId(annotation.id)}
                  size="xs"
                  variant={selectedAnnotationId === annotation.id ? "filled" : "default"}
                >
                  {index + 1}
                </Button>
              ))}
            </Group>
          ) : (
            <Text c="dimmed" size="sm">
              点击图像添加标记点。
            </Text>
          )}

          {selectedAnnotation ? (
            <Stack
              gap={1}
              p={1}
              style={{
                background: "var(--kb-panel-soft)",
                border: "2px solid var(--kb-line)",
                borderRadius: 0,
                boxShadow: "var(--kb-hard-shadow-sm)",
              }}
            >
              <Group align="center" gap={1} justify="space-between" wrap="nowrap">
                <Text c="white" fw={800} size="sm">
                  标注 {selectedAnnotationIndex + 1}
                </Text>
                <ActionIcon
                  aria-label="删除标注"
                  color="red"
                  onClick={() => {
                    const annotationId = selectedAnnotation?.id;
                    if (!annotationId) return;
                    updateRecipe((currentRecipe) => removeAnnotationMark(currentRecipe, annotationId));
                    setSelectedAnnotationId(null);
                  }}
                  size="sm"
                  variant="subtle"
                >
                  <IconTrash size={16} />
                </ActionIcon>
              </Group>
              <Textarea
                data-testid={`annotation-note-input-${selectedAnnotation?.id ?? "none"}`}
                minRows={4}
                onChange={(event: ChangeEvent<HTMLTextAreaElement>) => {
                  const annotationId = selectedAnnotation?.id;
                  if (!annotationId) return;
                  updateRecipe((currentRecipe) =>
                    updateAnnotationNote(currentRecipe, annotationId, event.currentTarget.value),
                  );
                }}
                placeholder="输入这处需要保留、修正或强调的内容"
                value={selectedAnnotation?.note ?? ""}
              />
            </Stack>
          ) : recipe.annotations.length > 0 ? (
            <Text c="dimmed" data-testid="annotation-select-hint" size="sm">
              选择一个标记点后填写说明。
            </Text>
          ) : null}
        </Stack>
      );
    }

    if (activeTool === "local-generate") {
      const hasMask = hasLocalMaskPaint(localMaskStrokes);
      const canSubmitLocalGenerate = hasMask && !isRegenerating;

      return (
        <Stack gap={1.25} data-testid="local-generate-controls">
          <Group gap={0.75} wrap="wrap">
            <Button
              data-testid="local-mask-mode-brush"
              onClick={() => setLocalMaskMode("brush")}
              size="xs"
              variant={localMaskMode === "brush" ? "filled" : "light"}
            >
              笔刷
            </Button>
            <Button
              data-testid="local-mask-mode-erase"
              onClick={() => setLocalMaskMode("erase")}
              size="xs"
              variant={localMaskMode === "erase" ? "filled" : "light"}
            >
              橡皮
            </Button>
            <Button data-testid="local-mask-clear" onClick={() => setLocalMaskStrokes([])} size="xs" variant="light">
              清空
            </Button>
          </Group>

          <Group align="center" gap={1} wrap="nowrap">
            <Text c="dimmed" miw={42} size="sm">
              笔刷
            </Text>
            <Slider
              data-testid="local-mask-radius"
              max={120}
              min={8}
              onChange={setLocalMaskRadius}
              step={2}
              style={{ flex: 1 }}
              value={localMaskRadius}
            />
            <Text c="dimmed" miw={34} size="sm" ta="right">
              {localMaskRadius}
            </Text>
          </Group>

          <Textarea
            data-testid="editor-local-generate-note"
            minRows={3}
            onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setLocalEditNote(event.currentTarget.value)}
            placeholder="描述这块要怎么改"
            value={localEditNote}
          />

          {false && localReferenceOptions.length > 0 ? (
            <Stack gap={0.75}>
              <Text c="white" fw={700} size="sm">
                参考图
              </Text>
              <Group gap={0.75} wrap="wrap">
                {localReferenceOptions.map((option) => {
                  const selected = selectedLocalReferenceKeys.includes(option.key);
                  return (
                    <Button
                      key={option.key}
                      data-testid={`editor-local-reference-${option.key}`}
                      onClick={() => toggleLocalReferenceKey(option.key)}
                      size="xs"
                      variant={selected ? "filled" : "light"}
                    >
                      {option.label}
                    </Button>
                  );
                })}
              </Group>
            </Stack>
          ) : null}

          <Box
            accept="image/png,image/jpeg,image/webp"
            component="input"
            data-testid="editor-local-reference-file-input"
            onChange={handleLocalReferenceSelected}
            ref={localReferenceInputRef}
            style={{ display: "none" }}
            type="file"
          />
          <Group gap={0.75} wrap="wrap">
            <Button
              data-testid="editor-local-reference-upload"
              onClick={() => localReferenceInputRef.current?.click()}
              size="xs"
              variant="light"
            >
              {localUploadedReferenceFile ? "更换参考图" : "上传参考图"}
            </Button>
            {localUploadedReferenceFile ? (
              <Text c="dimmed" size="sm">
                {localUploadedReferenceFile.name}
              </Text>
            ) : null}
          </Group>
          {false && localUploadedReferenceFile ? (
            <Textarea
              data-testid="editor-local-reference-description"
              minRows={2}
              onChange={(event: ChangeEvent<HTMLTextAreaElement>) =>
                setLocalUploadedReferenceDescription(event.currentTarget.value)
              }
              placeholder="这张图参考什么"
              value={localUploadedReferenceDescription}
            />
          ) : null}

          {!hasMask ? (
            <Text c="dimmed" size="sm">
              先涂抹要修改的区域
            </Text>
          ) : null}
          {localGenerateError ? (
            <Alert color="red" data-testid="editor-local-generate-error" role="alert" variant="light">
              {localGenerateError}
            </Alert>
          ) : null}
          <Button
            data-testid="editor-local-generate-submit"
            disabled={!canSubmitLocalGenerate}
            loading={isRegenerating}
            onClick={() => void handleLocalGenerate()}
            size="sm"
            variant="filled"
          >
            局部生成
          </Button>
        </Stack>
      );
    }

    if (activeTool === "face") {
      return (
        <FaceControls
          compact={isMobileEditor}
          debugValues={landmarkDebugMode}
          onChange={handleFaceControlChange}
          onReset={handleFaceControlReset}
          onSliderInteractionEnd={endParameterInteraction}
          onSliderInteractionStart={beginParameterInteraction}
          values={recipe.face}
        />
      );
    }

    if (activeTool === "eyes") {
      return (
        <EyeControls
          compact={isMobileEditor}
          debugValues={landmarkDebugMode}
          onChange={handleEyeControlChange}
          onReset={handleEyeControlReset}
          onSliderInteractionEnd={endParameterInteraction}
          onSliderInteractionStart={beginParameterInteraction}
          values={recipe.eyes}
        />
      );
    }

    if (activeTool === "mouth") {
      return (
        <MouthControls
          compact={isMobileEditor}
          debugValues={landmarkDebugMode}
          onChange={handleMouthControlChange}
          onReset={handleMouthControlReset}
          onSliderInteractionEnd={endParameterInteraction}
          onSliderInteractionStart={beginParameterInteraction}
          values={recipe.mouth}
        />
      );
    }

    if (activeTool === "liquify") {
      return (
        <LiquifyControls
          brushRadius={brushRadius}
          canRedo={canRedo}
          canUndo={canUndo}
          compact={isMobileEditor}
          onBrushRadiusChange={handleLiquifyBrushRadiusChange}
          onBrushRadiusReset={handleLiquifyBrushRadiusReset}
          onRedo={handleRedo}
          onScaleChange={handleLiquifyScaleAmountChange}
          onScaleReset={handleLiquifyScaleAmountReset}
          onToolModeChange={setLiquifyToolMode}
          onUndo={handleUndo}
          onWarpStrengthChange={handleLiquifyWarpStrengthChange}
          onWarpStrengthReset={handleLiquifyWarpStrengthReset}
          scaleAmount={liquifyScaleAmount}
          toolMode={liquifyToolMode}
          warpStrength={liquifyWarpStrength}
        />
      );
    }

    return null;
  }

  function renderLandmarkDebugPanel() {
    if (!landmarkDebugMode) return null;

    const scores = landmarkDebugInfo?.points.map((point) => point.score) ?? [];
    const scoreSummary =
      scores.length > 0
        ? {
            average: scores.reduce((sum, score) => sum + score, 0) / scores.length,
            max: Math.max(...scores),
            min: Math.min(...scores),
          }
        : null;
    const lowScorePoints = [...(landmarkDebugInfo?.points ?? [])]
      .sort((left, right) => left.score - right.score)
      .slice(0, 6);
    const debugControlRows: Array<[string, LandmarkPoint]> = currentLandmarks
      ? [
          ["leftEye", currentLandmarks.leftEye],
          ["rightEye", currentLandmarks.rightEye],
          ["chin", currentLandmarks.chin],
          ["jawLeft", currentLandmarks.jawLeft],
          ["jawRight", currentLandmarks.jawRight],
          ["mouthLeft", currentLandmarks.mouthLeft],
          ["mouthCenter", currentLandmarks.mouthCenter],
          ["mouthRight", currentLandmarks.mouthRight],
        ]
      : [];
    const formatNormalizedPoint = (point: { x: number; y: number }) =>
      `${Math.round(point.x * stageWidth)}, ${Math.round(point.y * stageHeight)}`;
    const formatImagePoint = (point: { x: number; y: number }) =>
      `${Math.round(point.x)}, ${Math.round(point.y)}`;
    const formatBox = (box: AnimeLandmarkDebugBox | null | undefined) =>
      box
        ? `x=${Math.round(box.x)} y=${Math.round(box.y)} w=${Math.round(box.width)} h=${Math.round(box.height)}${
            box.score === undefined ? "" : ` score=${box.score.toFixed(3)}`
          }`
        : "none";

    return (
      <Box
        data-testid="landmark-debug-panel"
        p={1}
        style={{
          backdropFilter: "blur(10px)",
          background: "rgba(2, 6, 23, 0.82)",
          border: "1px solid rgba(96, 165, 250, 0.34)",
          borderRadius: 8,
          bottom: 10,
          boxShadow: "0 14px 34px rgba(0, 0, 0, 0.35)",
          color: "#dbeafe",
          maxHeight: "42%",
          maxWidth: "min(360px, calc(100% - 20px))",
          overflow: "auto",
          pointerEvents: "none",
          position: "absolute",
          right: 10,
          width: 340,
          zIndex: 12,
        }}
      >
        <Stack gap={0.5}>
          <Text c="white" fw={800} size="xs">
            Landmark Debug
          </Text>
          <Text component="div" size="xs" style={{ color: "rgba(191, 219, 254, 0.86)", lineHeight: 1.35 }}>
            图像:{" "}
            {landmarkDebugInfo
              ? `${landmarkDebugInfo.imageWidth} x ${landmarkDebugInfo.imageHeight}`
              : `${stageWidth} x ${stageHeight}`}
            <br />
            Face bbox: {formatBox(landmarkDebugInfo?.faceBox)}
            <br />
            HRNet bbox: {formatBox(landmarkDebugInfo?.hrnetBox)}
            <br />
            HRNet 后端: {landmarkDebugInfo?.hrnetProvider ?? "none"}
            <br />
            识别耗时: {landmarkDebugInfo ? `${landmarkDebugInfo.detectionMs}ms` : "none"}
            <br />
            点数: {landmarkDebugInfo?.points.length ?? 0}
            {scoreSummary ? (
              <>
                <br />
                Score: avg {scoreSummary.average.toFixed(3)} / min {scoreSummary.min.toFixed(3)} / max{" "}
                {scoreSummary.max.toFixed(3)}
              </>
            ) : null}
          </Text>
          <Divider color="var(--kb-line)" />
          <Text component="div" size="xs" style={{ color: "rgba(226, 232, 240, 0.86)", lineHeight: 1.35 }}>
            控制点:
            {debugControlRows.length
              ? debugControlRows.map(([label, point]) => (
                  <span key={label}>
                    <br />
                    {label}: {formatNormalizedPoint(point)}
                  </span>
                ))
              : " none"}
          </Text>
          <Text component="div" size="xs" style={{ color: "rgba(250, 204, 21, 0.9)", lineHeight: 1.35 }}>
            低置信度点:
            {lowScorePoints.length
              ? lowScorePoints.map((point) => (
                  <span key={point.index}>
                    <br />#{point.index}: {formatImagePoint(point)} score={point.score.toFixed(3)}
                  </span>
                ))
              : " none"}
          </Text>
        </Stack>
      </Box>
    );
  }

  function renderExtraReferenceControls() {
    if (!onRegenerate && !onSecondaryRegenerate) return null;

    return (
      <Stack gap={1.25}>
        <Divider color="var(--kb-line)" />
        <Group align="center" gap={1} justify="space-between" wrap="nowrap">
          <Box>
            <Text c="white" fw={700} size="sm">
              补充参考
            </Text>
            <Text c="dimmed" size="xs">
              可只写文字，也可上传图片辅助对齐
            </Text>
          </Box>
          {extraReferenceFile ? (
            <ActionIcon aria-label="清除补充参考" color="red" onClick={clearExtraReference} size="sm" variant="subtle">
              <IconTrash size={16} />
            </ActionIcon>
          ) : null}
        </Group>
        <Box
          accept="image/png,image/jpeg,image/webp"
          component="input"
          onChange={handleExtraReferenceSelected}
          ref={extraReferenceInputRef}
          style={{ display: "none" }}
          type="file"
        />
        <Button
          fullWidth
          leftSection={<IconPhoto size={16} />}
          onClick={() => extraReferenceInputRef.current?.click()}
          size="sm"
          variant="light"
        >
          {extraReferenceFile ? "更换参考图" : "上传参考图"}
        </Button>
        {extraReferenceFile && extraReferencePreviewUrl ? (
          <Group
            align="center"
            gap={1}
            p={0.75}
            style={{
              background: "var(--kb-panel-soft)",
              border: "2px solid var(--kb-line)",
              borderRadius: 0,
              minWidth: 0,
            }}
            wrap="nowrap"
          >
            <Box
              alt="补充参考预览"
              component="img"
              data-testid="extra-reference-preview"
              src={extraReferencePreviewUrl}
              style={{
                aspectRatio: "1 / 1",
                border: "2px solid var(--kb-line)",
                borderRadius: 0,
                flex: "0 0 52px",
                height: 52,
                objectFit: "cover",
                width: 52,
              }}
            />
            <Text
              c="dimmed"
              size="xs"
              style={{ minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
            >
              {extraReferenceFile.name}
            </Text>
          </Group>
        ) : null}
        <Textarea
          minRows={2}
          onChange={(event: ChangeEvent<HTMLTextAreaElement>) => setExtraReferenceDescription(event.currentTarget.value)}
          placeholder="例如：嘴型更委屈，眼神更柔和；上传图片时也会作为参考图说明"
          value={extraReferenceDescription}
        />
      </Stack>
    );
  }

  const editorPanelStyle = {
    background:
      "repeating-linear-gradient(0deg, rgba(25,31,35,0.025) 0 1px, transparent 1px 5px), var(--kb-panel)",
    border: "3px solid var(--kb-line)",
    borderRadius: 0,
    boxShadow: "var(--kb-hard-shadow)",
  };

  return (
    <Paper className="grunge-card" component="section" p={{ base: 2, md: 3 }} shadow="sm" withBorder>
      <Stack gap={2}>
        <EditorToolbar
          isComparingOriginal={compareOriginal}
          isRecognizingFace={isDetectingLandmarks}
          isRegenerating={isRegenerating}
          regenerateLabel={regenerateLabel}
          secondaryRegenerateLabel={secondaryRegenerateLabel}
          showRegenerateActions={showRegenerateActions}
          onCompareEnd={() => setCompareOriginal(false)}
          onCompareStart={() => setCompareOriginal(true)}
          onClearImage={onClearImage}
          onFit={() => {
            setZoom(1);
            setPan({ x: 0, y: 0 });
          }}
          onRegenerate={onRegenerate ? () => void handleRegenerate() : undefined}
          onRecognizeFace={canShowLandmarkToggle ? () => void recognizeFaceLandmarks() : undefined}
          onResetAll={resetAllAdjustments}
          onSecondaryRegenerate={onSecondaryRegenerate ? () => void handleSecondaryRegenerate() : undefined}
          onSave={() => void handleSave()}
          onZoomChange={handleZoomChange}
          showViewportActions={false}
          zoomPercent={zoomPercent}
        />

        <Box
          data-parameters-position={isMobileEditor ? "bottom" : "right"}
          data-testid="editor-shell"
          style={{
            alignItems: "stretch",
            display: isMobileEditor ? "flex" : "grid",
            flexDirection: isMobileEditor ? "column" : undefined,
            gap: isMobileEditor ? 12 : 16,
            gridTemplateColumns: isMobileEditor ? undefined : "144px minmax(420px, 1fr) minmax(300px, 340px)",
            minHeight: isMobileEditor ? undefined : "min(760px, calc(100vh - 220px))",
            overflowX: isMobileEditor ? "visible" : "auto",
          }}
        >
          {!isMobileEditor && (
            <Box
              p={1}
              style={{
                ...editorPanelStyle,
                gridColumn: 1,
              }}
            >
              <EditorToolRail activeTool={activeTool} tools={enabledTools} onToolChange={handleToolChange} />
            </Box>
          )}

          <Stack
            gap={1}
            style={{
              flex: isMobileEditor ? "0 0 auto" : undefined,
              gridColumn: isMobileEditor ? undefined : 2,
              height: isMobileEditor ? "auto" : "100%",
              minWidth: 0,
            }}
          >
            <Box
              data-testid="editor-viewport-toolbar-card"
              p={isMobileEditor ? 0.75 : 1}
              style={{
                ...editorPanelStyle,
                alignSelf: "stretch",
                boxShadow: "var(--kb-hard-shadow-sm)",
                minWidth: 0,
              }}
            >
              <EditorToolbar
                isComparingOriginal={compareOriginal}
                landmarkDebugMode={landmarkDebugMode}
                landmarksVisible={showLandmarkControls}
                onCompareEnd={() => setCompareOriginal(false)}
                onCompareStart={() => setCompareOriginal(true)}
                onFit={() => {
                  setZoom(1);
                  setPan({ x: 0, y: 0 });
                }}
                onToggleLandmarks={
                  canShowLandmarkToggle
                    ? () => {
                        setTemporarilyHideLandmarks(false);
                        setShowLandmarkControls((current) => !current);
                      }
                    : undefined
                }
                onToggleLandmarkDebugMode={
                  canShowLandmarkToggle
                    ? () => {
                        setTemporarilyHideLandmarks(false);
                        setLandmarkDebugMode((current) => !current);
                      }
                    : undefined
                }
                onZoomChange={handleZoomChange}
                showCompareAction={false}
                showLandmarkToggle={canShowLandmarkToggle}
                showPrimaryActions={false}
                zoomPercent={zoomPercent}
              />
            </Box>
            <Box
              aria-label={imageUrl ? `候选 ${candidateIndex} 编辑画布` : "图像编辑画布"}
              data-testid="editor-stage"
              onContextMenu={handleStageContextMenu}
              onPointerCancel={handleStagePointerCancel}
              onPointerDown={handleStagePointerDown}
              onPointerLeave={handleStagePointerLeave}
              onPointerMove={handleStagePointerMove}
              onPointerUp={handleStagePointerUp}
              ref={stageRef}
              role="img"
              style={{
                alignItems: "center",
                background:
                  "repeating-linear-gradient(0deg, rgba(25,31,35,0.018) 0 1px, transparent 1px 5px), var(--kb-panel-soft)",
                border: "3px solid var(--kb-line)",
                borderRadius: 0,
                boxShadow: "var(--kb-hard-shadow)",
                cursor: activeTool === "face" || activeTool === "eyes" || activeTool === "mouth" ? "grab" : "crosshair",
                display: "flex",
                flex: 1,
                aspectRatio: isMobileEditor ? stageAspectRatio : undefined,
                height: isMobileEditor ? "auto" : "100%",
                justifyContent: "center",
                maxHeight: isMobileEditor ? "min(58dvh, 560px)" : undefined,
                minHeight: isMobileEditor ? 280 : 520,
                overflow: "hidden",
                position: "relative",
                touchAction: "none",
                userSelect: "none",
                width: "100%",
              }}
            >
              {imageUrl ? (
                <>
                  {showLandmarkHint && effectiveShowLandmarks && currentLandmarks ? (
                    <Alert
                      color="blue"
                      className="landmark-correction-hint"
                      data-testid="landmark-correction-hint"
                      style={{
                        left: isMobileEditor ? 10 : "50%",
                        maxWidth: isMobileEditor ? "min(50%, 176px)" : "min(92%, 520px)",
                        pointerEvents: "none",
                        position: "absolute",
                        top: isMobileEditor ? 10 : 12,
                        transform: isMobileEditor ? undefined : "translateX(-50%)",
                        zIndex: 8,
                      }}
                      variant="light"
                    >
                      {isMobileEditor ? "可拖动关键点修正。" : "如果关键点识别不准，可以直接拖动关键点自行修正。"}
                    </Alert>
                  ) : null}
                  {isDetectingLandmarks ? (
                    <Paper
                      className="landmark-loading-card"
                      data-testid="landmark-loading-indicator"
                      style={{
                        alignItems: "center",
                        display: "flex",
                        gap: 10,
                        left: "50%",
                        maxWidth: "min(92%, 360px)",
                        pointerEvents: "none",
                        position: "absolute",
                        top: showLandmarkHint && effectiveShowLandmarks && currentLandmarks ? 68 : 12,
                        transform: "translateX(-50%)",
                        zIndex: 9,
                      }}
                    >
                      <span aria-hidden="true" className="landmark-loading-spinner" />
                      <Text c="var(--kb-ink)" fw={900} size="sm">
                        正在识别关键点
                      </Text>
                    </Paper>
                  ) : null}
                  <Box
                    data-testid="editor-transform-layer"
                    style={{
                      inset: 0,
                      position: "absolute",
                      transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                      transformOrigin: "center",
                      transition: "transform 120ms ease",
                    }}
                  >
                    <Box
                      data-testid="editor-image-viewport"
                      ref={imageViewportRef}
                      style={{
                        height: imageViewportRect.height > 0 ? imageViewportRect.height : "100%",
                        left: imageViewportRect.width > 0 ? imageViewportRect.left : 0,
                        position: "absolute",
                        top: imageViewportRect.height > 0 ? imageViewportRect.top : 0,
                        visibility: imageViewportReady ? "visible" : "hidden",
                        width: imageViewportRect.width > 0 ? imageViewportRect.width : "100%",
                      }}
                    >
                      <Box
                        alt=""
                        component="img"
                        data-testid="editor-base-image"
                        onLoad={handleBaseImageLoad}
                        ref={baseImageRef}
                        src={imageUrl}
                        style={{
                          filter: compareOriginal ? undefined : `saturate(${1 + recipePreview.liquifyIntensity * 0.04})`,
                          height: "100%",
                          inset: 0,
                          objectFit: "contain",
                          opacity: compareOriginal || pixiStageFailed || !pixiVisualReady ? 1 : 0,
                          pointerEvents: "none",
                          position: "absolute",
                          transform: compareOriginal ? undefined : fallbackPreviewTransform,
                          transition: "filter 120ms ease, opacity 80ms ease, transform 120ms ease",
                          width: "100%",
                        }}
                      />
                      <Box
                        data-testid="pixi-host"
                        ref={setPixiHostElement}
                        style={{
                          height: "100%",
                          inset: 0,
                          opacity: compareOriginal || pixiStageFailed ? 0 : 1,
                          pointerEvents: "none",
                          position: "absolute",
                          transition: "opacity 80ms ease",
                          width: "100%",
                        }}
                      />
                      <AnnotationLayer
                        annotations={activeTool === "annotation" ? recipe.annotations : []}
                        brushPreview={visibleLiquifyBrushPreview}
                        debugFaceBox={normalizedDebugFaceBox}
                        debugHrnetBox={normalizedDebugHrnetBox}
                        debugLandmarks={landmarkDebugInfo?.points}
                        eyeRegionScale={recipe.eyes.eyeRegionScale}
                        height={stageHeight}
                        landmarks={currentLandmarks}
                        liquifyStrokes={recipe.liquify}
                        onAnnotationDelete={handleAnnotationDelete}
                        onAnnotationHandlePointerDown={handleAnnotationHandlePointerDown}
                        onAnnotationPointerDown={handleAnnotationPointerDown}
                        onLandmarkPointerDown={handleLandmarkPointerDown}
                        selectedAnnotationId={selectedAnnotationId}
                        selectedLandmarkKey={selectedLandmarkKey}
                        secondaryLandmarks={secondaryLandmarks}
                        showLandmarkDebug={landmarkDebugMode}
                        showLandmarks={effectiveShowLandmarks || landmarkDebugMode}
                        showSecondaryLandmarks={landmarkDebugMode && !landmarkDebugInfo}
                        showLiquifyStrokes={activeTool === "liquify"}
                        visible={
                          activeTool === "annotation" ||
                          activeTool === "face" ||
                          activeTool === "eyes" ||
                          activeTool === "mouth" ||
                          activeTool === "liquify"
                        }
                        width={stageWidth}
                      />
                      {activeTool === "local-generate" ? (
                        <LocalMaskLayer
                          brushPreview={localMaskBrushPreview}
                          height={stageHeight}
                          strokes={localMaskStrokes}
                          width={stageWidth}
                        />
                      ) : null}
                    </Box>
                  </Box>
                  <Tooltip label="按住对比原图">
                    <ActionIcon
                      aria-label="按住对比原图"
                      color={compareOriginal ? "cyan" : "gray"}
                      data-testid="editor-compare-floating"
                      onBlur={() => setCompareOriginal(false)}
                      onKeyDown={(event: ReactKeyboardEvent<HTMLButtonElement>) => {
                        if (event.key !== " " && event.key !== "Enter") return;
                        event.preventDefault();
                        setCompareOriginal(true);
                      }}
                      onKeyUp={(event: ReactKeyboardEvent<HTMLButtonElement>) => {
                        if (event.key !== " " && event.key !== "Enter") return;
                        event.preventDefault();
                        setCompareOriginal(false);
                      }}
                      onPointerCancel={() => setCompareOriginal(false)}
                      onPointerDown={(event: PointerEvent<HTMLButtonElement>) => {
                        event.preventDefault();
                        event.stopPropagation();
                        event.currentTarget.setPointerCapture?.(event.pointerId);
                        setCompareOriginal(true);
                      }}
                      onPointerUp={(event: PointerEvent<HTMLButtonElement>) => {
                        event.preventDefault();
                        event.stopPropagation();
                        event.currentTarget.releasePointerCapture?.(event.pointerId);
                        setCompareOriginal(false);
                      }}
                      size="lg"
                      style={{
                        background: compareOriginal ? "var(--kb-dirty-yellow)" : "var(--kb-paper)",
                        border: "3px solid var(--kb-line)",
                        borderRadius: 0,
                        bottom: 12,
                        boxShadow: "var(--kb-hard-shadow-sm)",
                        color: "var(--kb-ink)",
                        position: "absolute",
                        right: 12,
                        zIndex: 12,
                      }}
                      variant={compareOriginal ? "filled" : "light"}
                    >
                      <CompareOriginalIcon size={20} />
                    </ActionIcon>
                  </Tooltip>
                  {renderLandmarkDebugPanel()}
                  {saveMenuPosition ? (
                    <Paper
                      data-testid="editor-save-menu"
                      onPointerDown={(event: PointerEvent<HTMLDivElement>) => {
                        event.stopPropagation();
                      }}
                      role="menu"
                      shadow="sm"
                      style={{
                        background: "var(--kb-panel)",
                        border: "3px solid var(--kb-line)",
                        borderRadius: 0,
                        boxShadow: "var(--kb-hard-shadow-sm)",
                        left: saveMenuPosition.x,
                        minWidth: 176,
                        padding: 8,
                        position: "absolute",
                        top: saveMenuPosition.y,
                        zIndex: 20,
                      }}
                      withBorder
                    >
                      <Stack gap={0.75}>
                        <Button
                          data-testid="editor-save-menu-save"
                          fullWidth
                          leftSection={<IconPhoto size={16} />}
                          onClick={() => {
                            setSaveMenuPosition(null);
                            void handleSave();
                          }}
                          role="menuitem"
                          size="sm"
                          variant="filled"
                        >
                          保存图像
                        </Button>
                        <Button
                          color="gray"
                          data-testid="editor-save-menu-cancel"
                          fullWidth
                          onClick={() => setSaveMenuPosition(null)}
                          role="menuitem"
                          size="sm"
                          variant="light"
                        >
                          取消
                        </Button>
                      </Stack>
                    </Paper>
                  ) : null}
                </>
              ) : (
                <Stack align="center" gap="sm" p="xl" ta="center">
                  <IconPhoto color="#64748b" size={48} />
                  <Title c="white" order={3} size="h4">
                    暂无候选图像
                  </Title>
                  <Text c="dimmed" maw={360} size="sm">
                    选择生成候选后，真实图像会显示在这里。
                  </Text>
                </Stack>
              )}
            </Box>
          </Stack>

          <Box
            data-testid="editor-controls-panel"
            p={isMobileEditor ? 1.5 : 2}
            style={{
              ...editorPanelStyle,
              flex: isMobileEditor ? "0 0 auto" : undefined,
              gridColumn: isMobileEditor ? undefined : 3,
              maxHeight: isMobileEditor ? "min(38dvh, 360px)" : undefined,
              minWidth: isMobileEditor ? 0 : 280,
              overflowY: isMobileEditor ? "auto" : undefined,
            }}
          >
            {isMobileEditor ? (
              <Stack gap="sm">
                <EditorToolRail
                  activeTool={activeTool}
                  orientation="horizontal"
                  tools={enabledTools}
                  onToolChange={handleToolChange}
                />
                <Divider color="var(--kb-line)" />
                {renderToolControls()}
                {activeTool === "annotation" ? renderExtraReferenceControls() : null}
              </Stack>
            ) : (
              <Stack gap={1.5}>
                <Group align="center" gap={1} justify="space-between" wrap="nowrap">
                  <Box>
                    <Title c="white" order={3} size="h4">
                      {activeToolDetail.title}
                    </Title>
                    <Text c="dimmed" mt={0.5} size="sm">
                      {activeToolDetail.description}
                    </Text>
                  </Box>
                </Group>
                <Divider color="var(--kb-line)" />
                {renderToolControls()}
                {activeTool === "annotation" ? renderExtraReferenceControls() : null}
              </Stack>
            )}
          </Box>
        </Box>
      </Stack>
    </Paper>
  );
});

function CompareOriginalIcon({ size = 20 }: { size?: number }) {
  return (
    <svg aria-hidden="true" fill="currentColor" height={size} viewBox="0 0 24 24" width={size}>
      <path d="M4 4h7v2H6v5H4V4m9 0h7v7h-2V6h-5V4M4 13h2v5h5v2H4v-7m14 0h2v7h-7v-2h5v-5M8 8h8v8H8V8m2 2v4h4v-4h-4Z" />
    </svg>
  );
}
