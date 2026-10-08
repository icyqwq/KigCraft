/**
 * View transitions are progressive: browsers without document.startViewTransition (and React Router, which then
 * skips it) just switch. The kind tells the stylesheet which parts of the page take part in this one.
 */
export type TransitionKind = "design" | "lightbox" | "workspace";

export function markTransition(kind: TransitionKind) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.dataset.vtKind = kind;
  window.setTimeout(() => { if (root.dataset.vtKind === kind) delete root.dataset.vtKind; }, 800);
}

export const sharedName = (revisionId: string) => "revision-" + revisionId;

/** The card image gives its name to the lightbox image: a name must be unique while the new page is captured. */
export function handOverSharedElement(revisionId: string, target: HTMLElement) {
  document.querySelectorAll<HTMLElement>("[data-vt-source]").forEach(node => { node.style.viewTransitionName = ""; });
  target.style.viewTransitionName = sharedName(revisionId);
  return () => { target.style.viewTransitionName = ""; };
}
