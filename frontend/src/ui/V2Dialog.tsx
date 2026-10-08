import { useEffect, useId, useRef, type ReactNode } from "react";
import Dialog from "@mui/material/Dialog";
import useMediaQuery from "@mui/material/useMediaQuery";
import { IconX } from "@tabler/icons-react";
import { useTranslation } from "react-i18next";
import "./v2-dialog.css";

export type V2DialogSize = "sm" | "md" | "lg";
type Props = {
  title: ReactNode;
  onClose: () => void;
  children?: ReactNode;
  /** Right-aligned footer: secondary action first, primary last. */
  actions?: ReactNode;
  open?: boolean;
  size?: V2DialogSize;
  /** Destructive confirmation: the primary action uses the danger colour. */
  danger?: boolean;
  /** When false, Esc, the scrim and × are ignored (an operation is in flight). */
  dismissible?: boolean;
  closeLabel?: string;
  className?: string;
  labelId?: string;
};
const widths: Record<V2DialogSize, number> = { sm: 420, md: 560, lg: 880 };
const firstField = ".v2-dialog-content :is(input:not([type=hidden]),textarea,select):not(:disabled)";

function Panel({ titleId, title, closeLabel, onClose, children, actions, danger, dismissible, className }:
  Omit<Props, "open" | "size" | "labelId"> & { titleId: string; closeLabel: string; dismissible: boolean }) {
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const root = panel.current;
    if (!root || root.contains(document.activeElement)) return;
    const target = root.querySelector<HTMLElement>(firstField)
      ?? root.querySelector<HTMLElement>(".v2-actions .v2-primary:not(:disabled)");
    target?.focus();
  }, []);
  return <div ref={panel} className={"v2-dialog-body v2-dialog-panel" + (danger ? " is-danger" : "") + (className ? " " + className : "")}>
    <header className="v2-dialog-head">
      <h2 id={titleId}>{title}</h2>
      <button type="button" className="v2-quiet v2-icon v2-dialog-close" aria-label={closeLabel} title={closeLabel}
        disabled={!dismissible} onClick={onClose}><IconX size={18} stroke={1.75} /></button>
    </header>
    <div className="v2-dialog-content">{children}</div>
    {actions ? <div className="v2-actions">{actions}</div> : null}
  </div>;
}

export function V2Dialog({ title, onClose, children, actions, open = true, size = "sm", danger = false, dismissible = true, closeLabel, className, labelId }: Props) {
  const { t } = useTranslation();
  const reduced = useMediaQuery("(prefers-reduced-motion: reduce)");
  const generated = useId();
  const titleId = labelId ?? "v2-dialog-title-" + generated.replace(/:/g, "");
  return <Dialog open={open} className={"v2-dialog is-" + size + (open ? "" : " is-closing")} maxWidth={false}
    aria-labelledby={titleId} transitionDuration={reduced ? 120 : { enter: 240, exit: 180 }}
    onClose={() => { if (dismissible) onClose(); }}
    slotProps={{ paper: { className: "v2-dialog-paper" + (open ? "" : " is-closing"), style: { width: "min(" + widths[size] + "px, calc(100vw - 32px))" } } }}>
    <Panel titleId={titleId} title={title} closeLabel={closeLabel ?? t("common.close")} onClose={onClose} actions={actions}
      danger={danger} dismissible={dismissible} className={className}>{children}</Panel>
  </Dialog>;
}
export default V2Dialog;
