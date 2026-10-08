import type { SubmitTurn } from "../../contracts/v2";
import type { HistoryTarget, RequestPreview } from "./api";
import type { ReferenceAttachment } from "../references/attachments";

export type PendingTurn = { designId: string; branchId: string; payload: SubmitTurn; units: number; attempted: boolean; preview?: RequestPreview };
export type ComposerDraft = { text: string; attachments: ReferenceAttachment[]; createKey: string; pending: PendingTurn | null; target: HistoryTarget | null;
  /** The user chose "no base image" for this turn: send base_revision_id null instead of the branch head. */
  noBase?: boolean };
export type ComposerScope = { ownerId: string | null; designId: string | null; branchId: string | null; purpose?:string };
/** Identifies the place a composer belongs to; a different place gets a fresh, empty composer. */
export const composerScopeKey = (scope: ComposerScope) => JSON.stringify([scope.ownerId, scope.designId, scope.branchId,...(scope.purpose?[scope.purpose]:[])]);
export const emptyComposerDraft = (): ComposerDraft => ({text:"",attachments:[],createKey:crypto.randomUUID(),pending:null,target:null});
let cleaned = false;
/** Earlier versions saved drafts in this browser. They are no longer used, so remove them once. */
export function discardSavedComposerDrafts() {
  if (cleaned) return;
  cleaned = true;
  try { indexedDB.deleteDatabase("kigcraft-composer-drafts"); } catch { /* storage unavailable */ }
  try {
    for (const store of [sessionStorage, localStorage]) {
      for (const key of Object.keys(store)) if (key.startsWith("kig-v2-draft:") || key.startsWith("kig-v2-composer-record:")) store.removeItem(key);
    }
  } catch { /* storage unavailable */ }
}
