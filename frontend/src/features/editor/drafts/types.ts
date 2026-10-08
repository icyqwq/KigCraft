import type { ReferenceAttachment } from "../../references/attachments";
import type { CloudState } from "./cloudTypes";
import type { EditorHistoryState } from "../core/history";

/** Blobs stay in IndexedDB; this object must never enter localStorage or query caches. */
export type EditorDraftContent = EditorHistoryState & {
  localReferences?: ReferenceAttachment[];
  localEditMode?: "preserve" | "lock_outside";
  localEditNote: string;
  selectedLocalReferenceKeys: string[];
  localUploadedReferenceFile: File | null;
  localUploadedReferenceDescription: string;
  extraReferenceFile: File | null;
  extraReferenceDescription: string;
};
export type DraftScope = {
  ownerId: string;
  branchId: string;
  baseRevisionId: string;
  rootAssetId: string;
};
export type DraftSource = {
  blob: Blob;
  sha256: string;
  width: number;
  height: number;
};
export type EditorDraftRecord = {
  schemaVersion: 1 | 2;
  id: string;
  scope: string;
  version: number;
  updatedAt: number;
  conflictOf?: string;
  cloud?: CloudState;
  source: DraftSource;
  content: EditorDraftContent;
};
export function draftScopeKey(scope: DraftScope) {
  if (Object.values(scope).some(value => !value)) throw new Error("Invalid draft scope");
  return JSON.stringify([scope.ownerId, scope.branchId, scope.baseRevisionId, scope.rootAssetId]);
}
