import { useId } from "react";
import type { LocalMaskStroke } from "../localGeneration";

export type LocalMaskLayerProps = {
  brushPreview: { radius: number; x: number; y: number } | null;
  height: number;
  strokes: readonly LocalMaskStroke[];
  width: number;
};

export function LocalMaskLayer({ brushPreview, height, strokes, width }: LocalMaskLayerProps) {
  const safeWidth = Math.max(1, width);
  const safeHeight = Math.max(1, height);
  const maskId = `local-mask-${useId().replace(/:/g, "")}`;

  return (
    <div
      data-testid="local-mask-layer"
      style={{
        inset: 0,
        pointerEvents: "none",
        position: "absolute",
        zIndex: 6,
      }}
    >
      <svg
        aria-hidden="true"
        data-testid="local-mask-svg"
        preserveAspectRatio="xMidYMid meet"
        viewBox={`0 0 ${safeWidth} ${safeHeight}`}
        style={{ display: "block", height: "100%", width: "100%" }}
      >
        <defs>
          <mask id={maskId} maskUnits="userSpaceOnUse" x={0} y={0} width={safeWidth} height={safeHeight}>
            <rect fill="black" height={safeHeight} width={safeWidth} x={0} y={0} />
            {strokes.map((stroke) => renderStroke(stroke, safeWidth, safeHeight))}
          </mask>
        </defs>
        <rect
          data-testid="local-mask-fill"
          fill="rgba(20,184,166,0.55)"
          height={safeHeight}
          mask={`url(#${maskId})`}
          width={safeWidth}
          x={0}
          y={0}
        />
        {brushPreview ? (
          <circle
            cx={brushPreview.x * safeWidth}
            cy={brushPreview.y * safeHeight}
            data-testid="local-mask-brush-preview"
            fill="none"
            r={Math.max(1, brushPreview.radius)}
            stroke="rgba(20,184,166,0.95)"
            strokeDasharray="8 6"
            strokeWidth={2}
          />
        ) : null}
      </svg>
    </div>
  );
}

function renderStroke(stroke: LocalMaskStroke, safeWidth: number, safeHeight: number) {
  if (stroke.points.length === 0) return null;
  const color = stroke.mode === "erase" ? "black" : "white";

  if (stroke.points.length === 1) {
    const point = stroke.points[0];
    return (
      <circle
        key={stroke.id}
        cx={point.x * safeWidth}
        cy={point.y * safeHeight}
        data-testid={`local-mask-stroke-${stroke.id}`}
        fill={color}
        r={Math.max(1, stroke.radius)}
      />
    );
  }

  const points = stroke.points.map((point) => `${point.x * safeWidth},${point.y * safeHeight}`).join(" ");
  return (
    <polyline
      key={stroke.id}
      data-testid={`local-mask-stroke-${stroke.id}`}
      fill="none"
      points={points}
      stroke={color}
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={Math.max(1, stroke.radius * 2)}
    />
  );
}
