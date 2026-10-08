import { publicUrl } from "./publicUrl";
import type { CSSProperties } from "react";

/**
 * The KigCraft loading mark: a ruler and a compass turning about one needle while the pencil draws.
 * One image used for every loading state. The animation lives in the SVG, which also switches itself to
 * a still frame under prefers-reduced-motion.
 */
export function LogoLoader({ size = 24, label, className, style, testId }: { size?: number; label?: string; className?: string; style?: CSSProperties; testId?: string }) {
  // Inside a status region the surrounding text names the state, so the mark itself stays silent.
  return <img src={publicUrl("/brand/kigcraft-loader.svg")} width={size} height={size} alt="" className={"kc-logo-loader" + (className ? " " + className : "")}
    aria-hidden={label ? undefined : true} aria-label={label} role={label ? "progressbar" : undefined}
    style={{ display: "inline-block", flex: "none", verticalAlign: "middle", pointerEvents: "none", ...style }} draggable={false} data-testid={testId} />;
}
