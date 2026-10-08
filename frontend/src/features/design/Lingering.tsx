import { useEffect, useRef, useState, type ReactNode } from "react";
import { useOverlayExitMs } from "./overlayTiming";

/**
 * Overlays are shown while the address has their parameter. When it goes away the overlay is kept for
 * its exit animation (`closing` is true, with the props it had) and only then unmounted.
 */
export function Lingering({ when, render }: { when: boolean; render: (closing: boolean) => ReactNode }) {
  const ms = useOverlayExitMs();
  const last = useRef(render);
  if (when) last.current = render;
  const [mounted, setMounted] = useState(when);
  useEffect(() => {
    if (when) { setMounted(true); return; }
    const timer = setTimeout(() => setMounted(false), ms);
    return () => clearTimeout(timer);
  }, [when, ms]);
  if (when) return <>{render(false)}</>;
  return mounted ? <>{last.current(true)}</> : null;
}
