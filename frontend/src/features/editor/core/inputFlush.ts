import { createContext, useContext, useLayoutEffect, useRef } from "react";

/** Per-editor registry: exporting one image cannot flush another editor's draft. */
export const EditorInputFlushContext = createContext<Set<() => void> | null>(null);
export function useEditorInputFlush(flush: () => void) {
  const registry = useContext(EditorInputFlushContext);
  const latest = useRef(flush);
  latest.current = flush;
  useLayoutEffect(() => {
    if (!registry) return;
    const run = () => latest.current();
    registry.add(run);
    return () => { registry.delete(run); };
  }, [registry]);
}
