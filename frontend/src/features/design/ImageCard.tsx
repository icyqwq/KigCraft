import { lazy, Suspense } from "react";
import { IconAdjustmentsHorizontal, IconDownload, IconLayoutGrid, IconRefresh, IconSparkles } from "@tabler/icons-react";
import { imageKindOf, type Revision } from "./api";
import { useDesignCopy } from "./useDesignCopy";
import { useStamp } from "./useStamp";
import { markTransition, sharedName } from "./transitions";
import { preloadEditor } from "./overlays";

const LegacySources = lazy(() => import("./LegacySources"));

export type ImageCardProps = {
  revision: Revision;
  /** Animate in: the image arrived after the page was opened. */
  isNew?: boolean;
  canRegenerate: boolean;
  canEdit: boolean;
  canViewsets: boolean;
  /** Offered only on reference images; the action itself is wired by the page. */
  canMakeHeadShell: boolean;
  viewsetsHint?: string;
  onOpen: () => void;
  onRegenerate: () => void;
  onEdit: () => void;
  onViewsets: () => void;
  onMakeHeadShell: () => void;
};

/** A generated image on a white mount: version stamp, ink line, hard shadow. */
export function ImageCard(props: ImageCardProps) {
  const { revision, isNew = false } = props;
  const { c } = useDesignCopy();
  const stamp = useStamp()(revision);
  const reference = imageKindOf(revision) === "reference";
  return <article data-history-id={"image:" + revision.id} data-image-kind={imageKindOf(revision)} className={"v2-image-card" + (isNew ? " is-new" : "")}>
    {revision.source_kind === "legacy" ? <Suspense fallback={null}><LegacySources revisionId={revision.id} /></Suspense> : null}
    <div className="v2-image-frame">
      <span className="v2-stamp v2-version-stamp">{stamp}</span>
      <button type="button" className="v2-image-preview" style={{ aspectRatio: revision.width && revision.height ? revision.width / revision.height : 1 }}
        onClick={event => {
          // The lightbox image takes this name over, so the picture grows out of the card.
          const image = event.currentTarget.querySelector("img");
          if (image) image.style.viewTransitionName = sharedName(revision.id);
          markTransition("lightbox"); props.onOpen();
        }} aria-label={c("viewImage") + " · " + stamp}>
        <img src={revision.image_url} alt={stamp} loading="lazy" data-vt-source={revision.id} />
      </button>
    </div>
    <div className="v2-image-actions">
      {reference ? <button type="button" className="v2-primary" disabled={!props.canMakeHeadShell} onClick={props.onMakeHeadShell}>
        <IconSparkles size={16} stroke={1.75} />{c("makeHeadShell")}</button> : null}
      <button type="button" className="v2-quiet" disabled={!props.canRegenerate} title={!revision.can_regenerate ? c("noOriginal") : undefined}
        onClick={props.onRegenerate}><IconRefresh size={16} stroke={1.75} />{c("regenerate")}</button>
      <button type="button" className="v2-quiet" disabled={!props.canEdit} onClick={props.onEdit} onPointerEnter={preloadEditor} onFocus={preloadEditor}>
        <IconAdjustmentsHorizontal size={16} stroke={1.75} />{c("enterEditor")}</button>
      <a className="v2-quiet v2-image-save" href={revision.image_url} download={"kigcraft-" + stamp.replace(/[^\w.-]+/g, "-") + ".jpg"}>
        <IconDownload size={16} stroke={1.75} />{c("saveImageLocal")}</a>
      <button type="button" className="v2-quiet" disabled={reference || !props.canViewsets}
        title={reference ? c("referenceNoViewsets") : props.viewsetsHint} onClick={props.onViewsets}>
        <IconLayoutGrid size={16} stroke={1.75} />{c("generateViewsets")}</button>
    </div>
  </article>;
}
