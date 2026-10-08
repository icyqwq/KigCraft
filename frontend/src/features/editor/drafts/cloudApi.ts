import { z } from "zod";
import type { DraftScope, DraftSource } from "./types";
import { inspectServedSource } from "./repository";
import type { CloudRecord, CloudRequest } from "./cloudTypes";
export class DraftSyncError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}
const receipt = z.object({ id: z.string(), version: z.number().int().positive(), deleted: z.boolean() });
async function call(path: string, signal: AbortSignal, init: RequestInit = {}) {
  const response = await fetch("/api/v2" + path, { ...init, signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]),
    credentials: "same-origin", headers: init.body instanceof FormData ? undefined : { "Content-Type": "application/json" } });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new DraftSyncError(response.status, body.code ?? body.detail?.code ?? "draft_sync_failed");
  }
  return response.json();
}
export const cloudDraftApi = {
  async list(scope: DraftScope, signal: AbortSignal): Promise<CloudRecord[]> {
    const response = await call("/branches/" + encodeURIComponent(scope.branchId) + "/editor-drafts?base_revision_id=" +
      encodeURIComponent(scope.baseRevisionId), signal);
    if (!Array.isArray(response.items)) throw new Error("Invalid draft list");
    for (const record of response.items) {
      if (record.branch_id !== scope.branchId || record.base_revision_id !== scope.baseRevisionId ||
          record.root_asset_id !== scope.rootAssetId || ![1,2].includes(record.document?.schema_version) ||
          !Number.isInteger(record.version) || record.version < 1) throw new Error("Unsupported cloud draft");
    }
    return response.items;
  },
  async put(id: string, request: CloudRequest, signal: AbortSignal) {
    return receipt.parse(await call("/editor-drafts/" + encodeURIComponent(id), signal, { method: "PUT", body: JSON.stringify(request) }));
  },
  async remove(id: string, version: number, requestId: string, signal: AbortSignal) {
    return receipt.parse(await call("/editor-drafts/" + encodeURIComponent(id), signal,
      { method: "DELETE", body: JSON.stringify({ client_request_id: requestId, expected_version: version }) }));
  },
  async upload(file: File, ownerId: string, signal: AbortSignal): Promise<string> {
    const data = new FormData(); data.append("file", file);
    return z.object({ id: z.string() }).parse(await call("/assets/uploads?expected_owner_id=" + encodeURIComponent(ownerId), signal,
      { method: "POST", body: data })).id;
  },
  async asset(id: string, signal: AbortSignal): Promise<Blob> {
    const response = await fetch("/api/v2/assets/" + encodeURIComponent(id), {
      signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), credentials: "same-origin" });
    if (!response.ok) throw new DraftSyncError(response.status, "asset_unavailable");
    const blob = await response.blob();
    if (blob.size > 128 * 1024 * 1024) throw new Error("Asset too large");
    return blob;
  },
  /** The draft root as a source: watermarked bytes, identified by the stored original's hash. */
  async source(id: string, signal: AbortSignal): Promise<DraftSource> {
    const response = await fetch("/api/v2/assets/" + encodeURIComponent(id), {
      signal: AbortSignal.any([signal, AbortSignal.timeout(10000)]), credentials: "same-origin" });
    if (!response.ok) throw new DraftSyncError(response.status, "asset_unavailable");
    return inspectServedSource(response);
  },
};
