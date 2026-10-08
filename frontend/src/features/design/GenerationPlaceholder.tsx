import { LogoLoader } from "../../ui/LogoLoader";

const STEPS = 4;

/**
 * Reserves the picture space at the target ratio while a task runs: a mounted frame with crop marks and the
 * KigCraft loading mark, with four ticks that fill as the real stage advances.
 */
export function GenerationPlaceholder({ stage, ratio, step = 3, leaving = false }: { stage: string; ratio: number; step?: number; leaving?: boolean }) {
  return <div className={"v2-image-card v2-pending-card" + (leaving ? " is-leaving" : "")} role="status">
    <div className="v2-image-frame is-placeholder">
      <div className="v2-placeholder-paper" style={{ aspectRatio: ratio }}>
        <span className="v2-crop is-tl" aria-hidden="true" /><span className="v2-crop is-tr" aria-hidden="true" />
        <span className="v2-crop is-bl" aria-hidden="true" /><span className="v2-crop is-br" aria-hidden="true" />
        <LogoLoader size={112} className="v2-placeholder-mark" />
      </div>
    </div>
    <div className="v2-stage-row">
      <span className="v2-stage-ticks" aria-hidden="true">{Array.from({ length: STEPS }, (_, index) => <span key={index} className={index < step ? "is-done" : ""} />)}</span>
      <p className="v2-stage">{stage}</p>
    </div>
  </div>;
}
