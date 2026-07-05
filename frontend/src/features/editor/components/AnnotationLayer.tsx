import type { PointerEvent } from "react";
import { IconTrash } from "@tabler/icons-react";
import type { EyeRegion, LandmarkPoint, ManualLandmarkKey, ManualLandmarks } from "../deformation/landmarks";
import type { AnnotationMark, DetailRegion, LiquifyStroke } from "../deformation/recipe";

export type AnnotationLayerProps = {
  annotations?: AnnotationMark[];
  brushPreview?: { active: boolean; mode: "scale" | "warp"; radius: number; x: number; y: number } | null;
  debugFaceBox?: { height: number; width: number; x: number; y: number } | null;
  debugHrnetBox?: { height: number; width: number; x: number; y: number } | null;
  debugLandmarks?: Array<LandmarkPoint & { index: number; score?: number }>;
  detailRegions?: DetailRegion[];
  eyeRegionScale?: number;
  height: number;
  landmarks?: ManualLandmarks;
  liquifyStrokes?: LiquifyStroke[];
  secondaryLandmarks?: LandmarkPoint[];
  selectedAnnotationId?: string | null;
  selectedLandmarkKey?: ManualLandmarkKey | null;
  showLandmarkDebug?: boolean;
  showLandmarks?: boolean;
  showLiquifyStrokes?: boolean;
  showSecondaryLandmarks?: boolean;
  visible?: boolean;
  width: number;
  onAnnotationHandlePointerDown?: (annotationId: string, handle: string, event: PointerEvent<HTMLDivElement>) => void;
  onAnnotationPointerDown?: (annotationId: string, event: PointerEvent<HTMLDivElement>) => void;
  onAnnotationDelete?: (annotationId: string, event: PointerEvent<HTMLButtonElement>) => void;
  onLandmarkPointerDown?: (key: ManualLandmarkKey, event: PointerEvent<HTMLDivElement>) => void;
};

const landmarkLabels: Array<{ key: ManualLandmarkKey; label: string }> = [
  { key: "leftEye", label: "左眼" },
  { key: "rightEye", label: "右眼" },
  { key: "chin", label: "下巴" },
  { key: "jawLeft", label: "左下颌" },
  { key: "jawRight", label: "右下颌" },
  { key: "mouthLeft", label: "左嘴角" },
  { key: "mouthCenter", label: "嘴巴" },
  { key: "mouthRight", label: "右嘴角" },
];

function toPercent(value: number) {
  return `${Math.min(1, Math.max(0, value)) * 100}%`;
}

function normalizeDebugPoint(point: LandmarkPoint, width: number, height: number) {
  return {
    x: point.x > 1 ? point.x / Math.max(1, width) : point.x,
    y: point.y > 1 ? point.y / Math.max(1, height) : point.y,
  };
}

function scaledEyeRegion(region: EyeRegion | undefined, scaleValue = 20) {
  if (!region) return null;
  const multiplier = Math.min(1.4, Math.max(0.5, 1 + scaleValue / 100));
  return {
    radiusBottomY: region.radiusBottomY * multiplier,
    radiusTopY: region.radiusTopY * multiplier,
    radiusX: region.radiusX * multiplier,
  };
}

function annotationKind(annotation: AnnotationMark) {
  if (annotation.kind === "box") return "rect";
  if (annotation.kind === "callout") return "pin";
  return annotation.kind;
}

function annotationColor(annotation: AnnotationMark) {
  return annotation.color || "#ef4444";
}

function selectedActionPosition(annotation: AnnotationMark) {
  const kind = annotationKind(annotation);
  if (kind === "arrow" || kind === "rect") {
    return {
      x: Math.max(annotation.x, annotation.endX),
      y: Math.min(annotation.y, annotation.endY),
    };
  }
  if (kind === "text") {
    return {
      x: annotation.x + annotation.width / 2,
      y: annotation.y - annotation.height / 2,
    };
  }
  return {
    x: annotation.x,
    y: annotation.y,
  };
}

export function AnnotationLayer({
  annotations = [],
  brushPreview,
  debugFaceBox,
  debugHrnetBox,
  debugLandmarks = [],
  detailRegions = [],
  eyeRegionScale,
  height,
  landmarks,
  liquifyStrokes = [],
  secondaryLandmarks = [],
  selectedLandmarkKey,
  selectedAnnotationId,
  showLandmarkDebug = false,
  showLandmarks = false,
  showLiquifyStrokes = false,
  showSecondaryLandmarks = false,
  visible = true,
  width,
  onAnnotationHandlePointerDown,
  onAnnotationPointerDown,
  onAnnotationDelete,
  onLandmarkPointerDown,
}: AnnotationLayerProps) {
  if (!visible) return null;

  const stageTestId = showLandmarks ? "landmark-stage" : showLiquifyStrokes ? "liquify-stage" : "annotation-stage";
  const visibleLiquifyStrokes = showLiquifyStrokes ? liquifyStrokes.filter((stroke) => stroke.mode === "scale").slice(-1) : [];
  const visibleLiquifyStartIndex = Math.max(0, liquifyStrokes.indexOf(visibleLiquifyStrokes[0]));

  return (
    <div
      data-height={Math.max(1, Math.round(height))}
      data-testid={stageTestId}
      data-width={Math.max(1, Math.round(width))}
      style={{
        inset: 0,
        pointerEvents: "none",
        position: "absolute",
        zIndex: 3,
      }}
    >
      {annotations.map((annotation, index) => {
        const kind = annotationKind(annotation);
        const selected = selectedAnnotationId === annotation.id;
        const color = annotationColor(annotation);
        const label = String(index + 1);
        const rectLeft = Math.min(annotation.x, annotation.endX);
        const rectTop = Math.min(annotation.y, annotation.endY);
        const rectWidth = Math.max(0.01, Math.abs(annotation.endX - annotation.x) || annotation.width);
        const rectHeight = Math.max(0.01, Math.abs(annotation.endY - annotation.y) || annotation.height);
        const handleStyle = {
          background: "#e0f2fe",
          border: `2px solid ${color}`,
          boxShadow: "0 4px 10px rgba(15, 23, 42, 0.35)",
          height: 14,
          pointerEvents: "auto" as const,
          position: "absolute" as const,
          transform: "translate(-50%, -50%)",
          width: 14,
          zIndex: 5,
        };
        const selectedAction = selectedActionPosition(annotation);
        const deleteButton = selected ? (
          <button
            aria-label="删除标注"
            data-testid={`annotation-delete-${annotation.id}`}
            onPointerDown={(event) => {
              event.stopPropagation();
              onAnnotationDelete?.(annotation.id, event);
            }}
            style={{
              alignItems: "center",
              background: "var(--kb-panel)",
              border: "2px solid var(--kb-line)",
              boxShadow: "var(--kb-hard-shadow-sm)",
              color: "var(--kb-muted-red)",
              cursor: "pointer",
              display: "flex",
              height: 28,
              justifyContent: "center",
              left: toPercent(selectedAction.x),
              padding: 0,
              pointerEvents: "auto",
              position: "absolute",
              top: toPercent(selectedAction.y),
              transform: "translate(8px, -36px)",
              width: 28,
              zIndex: 8,
            }}
            type="button"
          >
            <IconTrash size={16} />
          </button>
        ) : null;

        if (kind === "arrow") {
          return (
            <div key={annotation.id}>
              <svg
                data-testid={`annotation-arrow-${index + 1}`}
                style={{
                  height: "100%",
                  inset: 0,
                  overflow: "visible",
                  pointerEvents: "auto",
                  position: "absolute",
                  width: "100%",
                }}
                viewBox="0 0 100 100"
                preserveAspectRatio="none"
              >
                <defs>
                  <marker
                    id={`annotation-arrow-head-${annotation.id}`}
                    markerHeight="7"
                    markerWidth="7"
                    orient="auto"
                    refX="6"
                    refY="3.5"
                  >
                    <polygon fill={color} points="0 0, 7 3.5, 0 7" />
                  </marker>
                </defs>
                <line
                  markerEnd={`url(#annotation-arrow-head-${annotation.id})`}
                  pointerEvents="none"
                  stroke={color}
                  strokeLinecap="round"
                  strokeWidth={Math.max(0.45, (annotation.strokeWidth ?? 4) / 8)}
                  x1={annotation.x * 100}
                  x2={annotation.endX * 100}
                  y1={annotation.y * 100}
                  y2={annotation.endY * 100}
                />
                <line
                  data-testid={`annotation-arrow-hit-${index + 1}`}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    onAnnotationPointerDown?.(annotation.id, event as unknown as PointerEvent<HTMLDivElement>);
                  }}
                  pointerEvents="stroke"
                  stroke="transparent"
                  strokeLinecap="round"
                  strokeWidth={4}
                  x1={annotation.x * 100}
                  x2={annotation.endX * 100}
                  y1={annotation.y * 100}
                  y2={annotation.endY * 100}
                />
              </svg>
              {selected ? (
                <div
                  data-testid={`annotation-handle-${annotation.id}-end`}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    onAnnotationHandlePointerDown?.(annotation.id, "end", event);
                  }}
                  style={{
                    ...handleStyle,
                    cursor: "crosshair",
                    left: toPercent(annotation.endX),
                    top: toPercent(annotation.endY),
                  }}
                />
              ) : null}
              {deleteButton}
            </div>
          );
        }

        if (kind === "rect") {
          return (
            <div key={annotation.id}>
              <div
                aria-label={`annotation ${label}`}
                aria-selected={selected}
                data-testid={`annotation-rect-${index + 1}`}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  onAnnotationPointerDown?.(annotation.id, event);
                }}
                style={{
                  background: selected ? `${color}22` : `${color}12`,
                  border: `${selected ? 3 : 2}px solid ${color}`,
                  boxShadow: selected ? "0 0 0 3px rgba(224, 242, 254, 0.22)" : "none",
                  cursor: "grab",
                  height: toPercent(rectHeight),
                  left: toPercent(rectLeft),
                  pointerEvents: "auto",
                  position: "absolute",
                  top: toPercent(rectTop),
                  width: toPercent(rectWidth),
                }}
              />
              {selected
                ? ([
                    ["nw", rectLeft, rectTop],
                    ["ne", rectLeft + rectWidth, rectTop],
                    ["sw", rectLeft, rectTop + rectHeight],
                    ["se", rectLeft + rectWidth, rectTop + rectHeight],
                  ] as const).map(([handle, x, y]) => (
                    <div
                      data-testid={`annotation-handle-${annotation.id}-${handle}`}
                      key={handle}
                      onPointerDown={(event) => {
                        event.stopPropagation();
                        onAnnotationHandlePointerDown?.(annotation.id, handle, event);
                      }}
                      style={{
                        ...handleStyle,
                        cursor: `${handle}-resize`,
                        left: toPercent(x),
                        top: toPercent(y),
                      }}
                    />
                  ))
                : null}
              {deleteButton}
            </div>
          );
        }

        if (kind === "text") {
          return (
            <div key={annotation.id}>
              <div
                aria-label={`annotation ${label}`}
                aria-selected={selected}
                data-testid={`annotation-text-${index + 1}`}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  onAnnotationPointerDown?.(annotation.id, event);
                }}
                style={{
                  border: selected ? `2px solid ${color}` : "2px solid transparent",
                  color,
                  cursor: "grab",
                  fontSize: annotation.fontSize ?? 24,
                  fontWeight: 900,
                  left: toPercent(annotation.x),
                  lineHeight: 1.15,
                  minHeight: 24,
                  minWidth: 34,
                  pointerEvents: "auto",
                  position: "absolute",
                  top: toPercent(annotation.y),
                  transform: "translate(-50%, -50%)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  width: toPercent(annotation.width),
                }}
              >
                {annotation.text?.trim() || "文字"}
              </div>
              {selected ? (
                <div
                  data-testid={`annotation-handle-${annotation.id}-se`}
                  onPointerDown={(event) => {
                    event.stopPropagation();
                    onAnnotationHandlePointerDown?.(annotation.id, "se", event);
                  }}
                  style={{
                    ...handleStyle,
                    cursor: "se-resize",
                    left: toPercent(annotation.x + annotation.width / 2),
                    top: toPercent(annotation.y + annotation.height / 2),
                  }}
                />
              ) : null}
              {deleteButton}
            </div>
          );
        }

        return (
          <div key={annotation.id}>
            <div
              aria-label={`annotation ${index + 1}`}
              aria-selected={selected}
              data-testid={`annotation-pin-${index + 1}`}
              onPointerDown={(event) => {
                event.stopPropagation();
                onAnnotationPointerDown?.(annotation.id, event);
              }}
              style={{
                alignItems: "center",
                background: selected ? "var(--kb-dirty-yellow)" : color,
                border: "3px solid rgba(255, 255, 255, 0.92)",
                borderRadius: 0,
                boxShadow: selected
                  ? "0 0 0 4px rgba(34, 211, 238, 0.24), var(--kb-hard-shadow-sm)"
                  : "var(--kb-hard-shadow-sm)",
                color: "#fff",
                cursor: "grab",
                display: "flex",
                fontSize: 14,
                fontWeight: 900,
                height: 30,
                justifyContent: "center",
                left: toPercent(annotation.x),
                lineHeight: 1,
                pointerEvents: "auto",
                position: "absolute",
                top: toPercent(annotation.y),
                transform: "translate(-50%, -50%)",
                width: 30,
              }}
            >
              {index + 1}
            </div>
            {deleteButton}
          </div>
        );
      })}

      {detailRegions.map((region) => {
        const diameter = `${Math.max(42, region.radius * 180)}px`;

        return (
          <div
            data-testid={`detail-region-${region.id}`}
            key={region.id}
            style={{
              background: "rgba(45, 212, 191, 0.12)",
              border: "2px dashed #2dd4bf",
              borderRadius: 999,
              height: diameter,
              left: toPercent(region.x),
              position: "absolute",
              top: toPercent(region.y),
              transform: "translate(-50%, -50%)",
              width: diameter,
            }}
          />
        );
      })}

      {showLandmarks && landmarks
        ? landmarkLabels.map(({ key, label }) => {
            const landmark = landmarks[key];
            if (!landmark) return null;
            const selected = selectedLandmarkKey === key;

            return (
              <div
                aria-label={`landmark ${label}`}
                aria-selected={selected}
                data-testid={`landmark-${key}`}
                key={key}
                onPointerDown={(event) => {
                  event.stopPropagation();
                  onLandmarkPointerDown?.(key, event);
                }}
                style={{
                  alignItems: "center",
                  cursor: "grab",
                  display: "flex",
                  height: 30,
                  justifyContent: "center",
                  left: toPercent(landmark.x),
                  pointerEvents: "auto",
                  position: "absolute",
                  top: toPercent(landmark.y),
                  transform: "translate(-50%, -50%)",
                  width: 30,
                }}
              >
                <span
                  style={{
                    background: "var(--kb-accent)",
                    border: "2px solid var(--kb-panel)",
                    borderRadius: 999,
                    boxShadow: selected
                      ? "0 0 0 7px rgba(209, 82, 47, 0.28), var(--kb-hard-shadow-sm)"
                      : "0 0 0 5px rgba(209, 82, 47, 0.16)",
                    display: "block",
                    height: 18,
                    opacity: 0.72,
                    width: 18,
                  }}
                />
              </div>
            );
          })
        : null}

      {showLandmarks && landmarks?.eyeRegions
        ? ([
            ["left", landmarks.leftEye, scaledEyeRegion(landmarks.eyeRegions.left, eyeRegionScale)],
            ["right", landmarks.rightEye, scaledEyeRegion(landmarks.eyeRegions.right, eyeRegionScale)],
          ] as const).map(([side, center, region]) => {
            if (!region) return null;
            const regionRadiusY = Math.max(region.radiusTopY, region.radiusBottomY);

            return (
              <div
                data-testid={`eye-region-${side}`}
                key={side}
                style={{
                  border: "2px dashed rgba(34, 211, 238, 0.78)",
                  borderRadius: "50%",
                  height: toPercent(regionRadiusY * 2),
                  left: toPercent(center.x),
                  pointerEvents: "none",
                  position: "absolute",
                  top: toPercent(center.y),
                  transform: "translate(-50%, -50%)",
                  width: toPercent(region.radiusX * 2),
                }}
              />
            );
          })
        : null}

      {showLandmarkDebug && debugFaceBox ? (
        <div
          data-testid="landmark-debug-face-box"
          style={{
            border: "2px dashed rgba(34, 197, 94, 0.7)",
            height: toPercent(debugFaceBox.height),
            left: toPercent(debugFaceBox.x),
            pointerEvents: "none",
            position: "absolute",
            top: toPercent(debugFaceBox.y),
            width: toPercent(debugFaceBox.width),
          }}
        />
      ) : null}

      {showLandmarkDebug && debugHrnetBox ? (
        <div
          data-testid="landmark-debug-hrnet-box"
          style={{
            border: "2px dotted rgba(96, 165, 250, 0.72)",
            height: toPercent(debugHrnetBox.height),
            left: toPercent(debugHrnetBox.x),
            pointerEvents: "none",
            position: "absolute",
            top: toPercent(debugHrnetBox.y),
            width: toPercent(debugHrnetBox.width),
          }}
        />
      ) : null}

      {showLandmarkDebug
        ? debugLandmarks.map((point) => {
            const normalized = normalizeDebugPoint(point, width, height);

            return (
              <div
                data-testid={`landmark-debug-point-${point.index}`}
                key={point.index}
                style={{
                  height: 7,
                  left: toPercent(normalized.x),
                  pointerEvents: "none",
                  position: "absolute",
                  top: toPercent(normalized.y),
                  transform: "translate(-50%, -50%)",
                  width: 7,
                  zIndex: 1,
                }}
              >
                <span
                  style={{
                    background: "rgba(250, 204, 21, 0.72)",
                    border: "1px solid rgba(24, 24, 27, 0.72)",
                    borderRadius: 999,
                    display: "block",
                    height: "100%",
                    width: "100%",
                  }}
                />
                <span
                  style={{
                    color: "rgba(250, 204, 21, 0.88)",
                    fontSize: 9,
                    left: "50%",
                    lineHeight: 1,
                    position: "absolute",
                    top: 8,
                    transform: "translateX(-50%)",
                  }}
                >
                  {point.index}
                </span>
              </div>
            );
          })
        : null}

      {showSecondaryLandmarks
        ? secondaryLandmarks.map((point, index) => (
            <span
              data-testid={`landmark-secondary-${index}`}
              key={`${point.x}-${point.y}-${index}`}
              style={{
                background: "rgba(250, 204, 21, 0.52)",
                borderRadius: 999,
                height: 6,
                left: toPercent(point.x),
                pointerEvents: "none",
                position: "absolute",
                top: toPercent(point.y),
                transform: "translate(-50%, -50%)",
                width: 6,
              }}
            />
          ))
        : null}

      {showLiquifyStrokes
        ? visibleLiquifyStrokes.map((stroke, index) => {
            const size =
              stroke.mode === "scale"
                ? Math.max(24, Math.min(320, stroke.radius * 2))
                : Math.max(18, Math.min(180, stroke.radius * 1.4));
            const isScaleStroke = stroke.mode === "scale";

            return (
              <div
                data-testid={`liquify-stroke-${visibleLiquifyStartIndex + index + 1}`}
                key={`${stroke.mode}-${stroke.x}-${stroke.y}-${stroke.radius}-${stroke.scale ?? ""}-${visibleLiquifyStartIndex + index}`}
                style={{
                  background: isScaleStroke
                    ? "radial-gradient(circle, rgba(216, 180, 254, 0.2) 0%, rgba(216, 180, 254, 0.08) 46%, rgba(216, 180, 254, 0.02) 72%, rgba(216, 180, 254, 0) 100%)"
                    : "radial-gradient(circle, rgba(103, 232, 249, 0.18) 0%, rgba(103, 232, 249, 0.07) 52%, rgba(103, 232, 249, 0) 100%)",
                  border: isScaleStroke
                    ? "2px dashed rgba(216, 180, 254, 0.92)"
                    : "2px dashed rgba(103, 232, 249, 0.92)",
                  borderRadius: 999,
                  boxShadow: "0 0 0 3px rgba(15, 23, 42, 0.18)",
                  height: size,
                  left: toPercent(stroke.x),
                  position: "absolute",
                  top: toPercent(stroke.y),
                  transform: "translate(-50%, -50%)",
                  width: size,
                }}
              />
            );
          })
        : null}

      {brushPreview ? (
        <div
          aria-hidden="true"
          data-active={brushPreview.active ? "true" : "false"}
          data-mode={brushPreview.mode}
          data-testid="liquify-brush-preview"
          style={{
            background: brushPreview.active
              ? "radial-gradient(circle, rgba(34, 197, 94, 0.24) 0%, rgba(34, 197, 94, 0.1) 55%, rgba(34, 197, 94, 0) 100%)"
              : brushPreview.mode === "scale"
                ? "radial-gradient(circle, rgba(216, 180, 254, 0.18) 0%, rgba(216, 180, 254, 0.08) 55%, rgba(216, 180, 254, 0) 100%)"
                : "radial-gradient(circle, rgba(103, 232, 249, 0.2) 0%, rgba(103, 232, 249, 0.08) 55%, rgba(103, 232, 249, 0) 100%)",
            border: brushPreview.active
              ? "2px solid rgba(34, 197, 94, 0.95)"
              : brushPreview.mode === "scale"
                ? "2px dashed rgba(216, 180, 254, 0.95)"
                : "2px dashed rgba(103, 232, 249, 0.95)",
            borderRadius: 999,
            boxShadow: "0 0 0 3px rgba(15, 23, 42, 0.18)",
            height: Math.max(18, Math.min(360, brushPreview.radius * 2)),
            left: toPercent(brushPreview.x),
            pointerEvents: "none",
            position: "absolute",
            top: toPercent(brushPreview.y),
            transform: "translate(-50%, -50%)",
            width: Math.max(18, Math.min(360, brushPreview.radius * 2)),
            zIndex: 7,
          }}
        />
      ) : null}
    </div>
  );
}

