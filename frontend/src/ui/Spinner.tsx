import type { CSSProperties } from "react";
import "./spinner.css";

/** Plain ring for buttons and small inline waits. The animated KigCraft mark (LogoLoader) is reserved for image generation. */
export function Spinner({ size = 18, label, className, testId }: { size?: number; label?: string; className?: string; testId?: string }) {
  return <span role={label ? "progressbar" : undefined} aria-label={label} aria-hidden={label ? undefined : true} data-testid={testId}
    className={"kc-spinner" + (className ? " " + className : "")} style={{ "--kc-spinner-size": size + "px" } as CSSProperties} />;
}
