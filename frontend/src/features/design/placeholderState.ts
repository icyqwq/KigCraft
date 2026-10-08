import { useEffect, useState } from "react";

/** Keeps a value for a moment after it goes away so the exit can be played. */
export function useLingering<T>(value: T | null | undefined, ms: number): { value: T | null; leaving: boolean } {
  const [kept, setKept] = useState<T | null>(value ?? null);
  const [leaving, setLeaving] = useState(false);
  useEffect(() => {
    if (value != null) { setKept(value); setLeaving(false); return; }
    if (kept == null) return;
    setLeaving(true);
    const timer = setTimeout(() => { setKept(null); setLeaving(false); }, ms);
    return () => clearTimeout(timer);
  }, [value]);
  return { value: value ?? kept, leaving: value == null && leaving };
}

/** Where a task is in queued -> review -> create -> save; unknown states count as creating. */
export function stageStep(state: string) {
  return state === "queued" ? 1 : state === "reviewing" ? 2 : state === "publishing" || state === "reconciling" ? 4 : 3;
}

