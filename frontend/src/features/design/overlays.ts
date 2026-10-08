/** Dynamic imports shared by React.lazy and by preloading, so a chunk is warm before its overlay opens. */
export const loadRevisionViewer = () => import("./RevisionViewerRoute");
export const loadFourViewDialog = () => import("./FourViewDialog");
export const loadEditorRoute = () => import("./EditorRoute");

/** Small overlays are fetched when the browser is idle; the heavy editor only when it is likely to be used. */
export function preloadOverlays() {
  const idle = (window as unknown as { requestIdleCallback?: (callback: () => void) => number }).requestIdleCallback ?? ((callback: () => void) => window.setTimeout(callback, 1200));
  idle(() => { void loadRevisionViewer(); void loadFourViewDialog(); });
}
export const preloadEditor = () => { void loadEditorRoute(); };
