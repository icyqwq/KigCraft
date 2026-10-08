import type { ReactNode } from "react";
import { IconArrowLeft, IconX } from "@tabler/icons-react";

/** One header for every workspace: back to the conversation, title, secondary actions, one main action. */
export function WorkspaceHeader({ title, titleId, backLabel, onBack, backDisabled = false, secondary, primary, dialog = false }: {
  title: ReactNode; titleId?: string; backLabel: string; onBack: () => void; backDisabled?: boolean; secondary?: ReactNode; primary?: ReactNode;
  /** Dialog form: title first, the close button last. */
  dialog?: boolean;
}) {
  if (dialog) return <header className="v2-workspace-header is-dialog">
    <h2 id={titleId}>{title}</h2>
    {secondary ? <div className="v2-workspace-secondary">{secondary}</div> : null}
    {primary}
    <button type="button" className="v2-quiet v2-workspace-close" disabled={backDisabled} onClick={onBack} aria-label={backLabel} title={backLabel}><IconX size={20} stroke={1.75} /></button>
  </header>;
  return <header className="v2-workspace-header">
    <button type="button" className="v2-quiet v2-workspace-back" disabled={backDisabled} onClick={onBack}><IconArrowLeft size={18} stroke={1.75} />{backLabel}</button>
    <h2 id={titleId}>{title}</h2>
    {secondary ? <div className="v2-workspace-secondary">{secondary}</div> : null}
    {primary}
  </header>;
}
