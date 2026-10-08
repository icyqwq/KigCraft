/** Shapes that match the final layout; no spinners or loading sentences. */
export function SidebarSkeleton() {
  return <div className="v2-skeleton-list" aria-hidden="true">
    {[0, 1, 2, 3, 4, 5].map(index => <div key={index} className="v2-skeleton-row">
      <span className="v2-skeleton v2-skeleton-thumb" />
      <span className="v2-skeleton v2-skeleton-line" style={{ width: 55 + (index * 13) % 35 + "%" }} />
    </div>)}
  </div>;
}
export function TimelineSkeleton() {
  return <div className="v2-timeline-skeleton" role="status" aria-busy="true">
    <span className="v2-skeleton v2-skeleton-bubble" />
    <span className="v2-skeleton v2-skeleton-line" style={{ width: "62%" }} />
    <span className="v2-skeleton v2-skeleton-line" style={{ width: "44%" }} />
    <span className="v2-skeleton v2-skeleton-image" />
  </div>;
}
/** The three editor columns as grey shapes; shown inside the dialog while the saved state and image load. */
export function EditorBodySkeleton() {
  return <div className="v2-editor-body-skeleton" role="status" aria-busy="true">
    <span className="v2-skeleton v2-skeleton-tools" />
    <span className="v2-skeleton v2-skeleton-canvas" />
    <span className="v2-skeleton v2-skeleton-params" />
  </div>;
}
/** The editor dialog's frame with grey content, so what follows only fills it in. */
export function EditorSkeleton() {
  return <div className="v2-editor-skeleton">
    <div className="v2-editor-skeleton-paper"><div className="v2-editor-skeleton-head"><span className="v2-skeleton v2-skeleton-line" style={{ width: 140 }} /></div><EditorBodySkeleton /></div>
  </div>;
}
export function OverlaySkeleton() {
  return <div className="v2-overlay-skeleton" role="status" aria-busy="true"><span className="v2-skeleton v2-overlay-skeleton-card" /></div>;
}
