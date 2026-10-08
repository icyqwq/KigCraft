import type { ReferenceAttachment } from "../../references/attachments";
import { cloudDraftApi, DraftSyncError } from "./cloudApi";
import type { CloudAttachment, CloudContent, CloudState, CloudReference } from "./cloudTypes";
import { editorDrafts, inspectDraftSource } from "./repository";
import { draftScopeKey, type DraftScope, type EditorDraftRecord } from "./types";


async function contentDigest(record: Pick<EditorDraftRecord, "content">) {
  const { localUploadedReferenceFile, extraReferenceFile, localReferences, ...content } = record.content;
  const fileDigest = async (file: File | null) => file ? {
    name: file.name, type: file.type, lastModified: file.lastModified,
    hash: Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer()))),
  } : null;
  const references = localReferences ? await Promise.all(localReferences.map(async item=>({
    key:item.key,usage:item.usage,...(item.assetId?{assetId:item.assetId}:{}),file:await fileDigest(item.file??null),
  }))) : undefined;
  // Match persisted fingerprints: UTF-16 key order, independent of the user's locale.
  const canonical = (value: unknown): unknown => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value)
      .sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)
      .map(([key, item]) => [key, canonical(item)]));
    return value;
  };
  return JSON.stringify(canonical({ content: {...content,...(references ? {localReferences:references}: {})}, local: await fileDigest(localUploadedReferenceFile), extra: await fileDigest(extraReferenceFile) }));
}

export class CloudDraftSync {
  constructor(private scope: DraftScope, private signal: AbortSignal) {}
  async sync(id: string) {
    if (!navigator.locks) throw new Error("Cloud draft locking unavailable");
    return navigator.locks.request("kigcraft-draft:" + draftScopeKey(this.scope) + ":" + id,
      { signal: this.signal }, () => this.lockedSync(id));
  }
  private async lockedSync(id: string) {
    this.signal.throwIfAborted();
    let record = await editorDrafts.get(this.scope, id);
    this.signal.throwIfAborted();
    if (!record) return undefined;
    let state: CloudState = record.cloud ?? { id: record.id, version: 0, uploads: {} };
    if (state.pendingDelete) {
      if (!state.pendingDelete.acknowledged) {
        try {
          await cloudDraftApi.remove(state.id, state.pendingDelete.version, state.pendingDelete.requestId, this.signal);
          this.signal.throwIfAborted();
          state = { ...state, pendingDelete: { ...state.pendingDelete, acknowledged: true } };
        } catch (error) {
          this.signal.throwIfAborted();
          if (!(error instanceof DraftSyncError) || error.code !== "draft_conflict") throw error;
          state = { ...state, pendingDelete: undefined, syncedContentDigest: undefined, syncedLocalVersion: undefined };
        }
        await editorDrafts.setCloud(this.scope, id, state);
      }
      if (state.pendingDelete) {
        const digest = await contentDigest(record);
        this.signal.throwIfAborted();
        if (digest === state.pendingDelete.contentDigest) return state;
        state = { id: crypto.randomUUID(), version: 0, uploads: state.uploads };
        await editorDrafts.setCloud(this.scope, id, state);
      }
    }
    // Finish an uncertain request before preparing newer content. The exact request survives reload.
    for (;;) {
      this.signal.throwIfAborted();
      if (!state.pending) {
        record = await editorDrafts.get(this.scope, id);
        this.signal.throwIfAborted();
        if (!record || state.syncedLocalVersion === record.version) return state;
        const fingerprint = await contentDigest(record);
        this.signal.throwIfAborted();
        if (state.syncedContentDigest === fingerprint) {
          state = { ...state, syncedLocalVersion: record.version };
          await editorDrafts.setCloud(this.scope, id, state);
          return state;
        }
        const upload = async (file: File) => {
          const hash = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())),
            value => value.toString(16).padStart(2, "0")).join("");
          this.signal.throwIfAborted();
          if (!state.uploads[hash]) {
            state.uploads[hash] = await cloudDraftApi.upload(file, this.scope.ownerId, this.signal);
            this.signal.throwIfAborted();
            await editorDrafts.setCloud(this.scope, id, state);
          }
          return state.uploads[hash];
        };
        const safeName=(name:string)=>name.replace(/[\\/\x00]/g,"_").slice(0,200)||"reference.png";
        const attachments: CloudAttachment[] = [];
        for (const slot of ["local", "extra"] as const) {
          if(slot==="local" && record.content.localReferences !== undefined) continue;
          const file = slot === "local" ? record.content.localUploadedReferenceFile : record.content.extraReferenceFile;
          if (!file) continue;
          attachments.push({slot,asset_id:await upload(file),file_name:safeName(file.name),last_modified:Math.max(0,file.lastModified)});
        }
        const { localUploadedReferenceFile: _local, extraReferenceFile: _extra, localReferences: localItems, ...legacyContent } = record.content;
        const references:CloudReference[]=[];
        for(const item of localItems??[]) {
          const assetId=item.assetId??(item.file?await upload(item.file):undefined);
          if(!assetId)throw new Error("Reference unavailable");
          references.push({key:item.key,assetId,usage:item.usage,...(item.file?{fileName:safeName(item.file.name),lastModified:Math.max(0,item.file.lastModified)}:{})});
        }
        const content:CloudContent={...legacyContent,...(localItems!==undefined?{localReferences:references}:{})};
        state = { ...state, pending: { localVersion: record.version, contentDigest: fingerprint, request: {
          client_request_id: crypto.randomUUID(), expected_version: state.version, device_id: record.id,
          branch_id: this.scope.branchId, base_revision_id: this.scope.baseRevisionId, root_asset_id: this.scope.rootAssetId,
          document: { schema_version: localItems === undefined ? 1 : 2, source_sha256: record.source.sha256, width: record.source.width,
            height: record.source.height, content, attachments },
        } } };
        this.signal.throwIfAborted();
        // Persist dispatch identity BEFORE the HTTP write; a lost response may not create a second draft.
        if (!await editorDrafts.setCloud(this.scope, id, state)) return undefined;
      }
      this.signal.throwIfAborted();
      try {
        const result = await cloudDraftApi.put(state.id, state.pending!.request, this.signal);
        this.signal.throwIfAborted();
        state = { ...state, version: result.version, syncedLocalVersion: state.pending!.localVersion, syncedContentDigest: state.pending!.contentDigest, pending: undefined };
        if (!await editorDrafts.setCloud(this.scope, id, state)) return undefined;
      } catch (error) {
        // A late rejection is no longer allowed to replace the persisted retry identity.
        this.signal.throwIfAborted();
        if (error instanceof DraftSyncError && error.code === "draft_conflict") {
          // Keep the remote record intact. This local edit gets its own cloud identity.
          state = { ...state, id: crypto.randomUUID(), version: 0, conflictCopy: true,
            pending: { ...state.pending!, request: { ...state.pending!.request, expected_version: 0, client_request_id: crypto.randomUUID() } } };
          if (!await editorDrafts.setCloud(this.scope, id, state)) return undefined;
          continue;
        }
        if (error instanceof DraftSyncError && [400, 422].includes(error.status)) {
          state = { ...state, pending: undefined };
          await editorDrafts.setCloud(this.scope, id, state);
        }
        throw error;
      }
    }
  }
  async remove(record: EditorDraftRecord) {
    if (!navigator.locks) return false;
    return navigator.locks.request("kigcraft-draft:" + draftScopeKey(this.scope) + ":" + record.id,
      { signal: this.signal }, async () => {
        const current = await editorDrafts.get(this.scope, record.id);
        this.signal.throwIfAborted();
        if (!current || current.version !== record.version) return false;
        let state = current.cloud;
        if (state?.pending) state = await this.lockedSync(record.id);
        this.signal.throwIfAborted();
        if (state && state.version > 0) {
          if (!state.pendingDelete) {
            state = { ...state, pendingDelete: { requestId: crypto.randomUUID(), version: state.version, contentDigest: await contentDigest(current) } };
            this.signal.throwIfAborted();
            if (!await editorDrafts.setCloud(this.scope, record.id, state)) return false;
          }
          try {
            if (!state.pendingDelete!.acknowledged) {
              await cloudDraftApi.remove(state.id, state.pendingDelete!.version, state.pendingDelete!.requestId, this.signal);
              this.signal.throwIfAborted();
              await editorDrafts.setCloud(this.scope, record.id, { ...state, pendingDelete: { ...state.pendingDelete!, acknowledged: true } });
            }
          } catch (error) {
            if (this.signal.aborted) return false;
            if (error instanceof DraftSyncError && error.code === "draft_conflict") {
              // The cloud has new work. Preserve it and allow this local copy to become a separate draft later.
              await editorDrafts.setCloud(this.scope, record.id, { ...state, pendingDelete: undefined, syncedContentDigest: undefined, syncedLocalVersion: undefined });
            }
            return false;
          }
        }
        this.signal.throwIfAborted();
        // Local writes do not take the network lock. A newer draft must not be discarded.
        const latest = await editorDrafts.get(this.scope, record.id);
        this.signal.throwIfAborted();
        return latest?.version === record.version;
      });
  }
}

/** Import remote versions as separate local copies. Never replace an editor's active content. */
export async function recoverCloudDrafts(scope: DraftScope, signal: AbortSignal) {
  if (!navigator.locks) throw new Error("Cloud draft locking unavailable");
  return navigator.locks.request("kigcraft-draft-recovery:" + draftScopeKey(scope), { signal }, async () => {
    for (const item of await editorDrafts.list(scope)) {
      if (!item.cloud?.pendingDelete) continue;
      const state = await new CloudDraftSync(scope, signal).sync(item.id);
      const current = await editorDrafts.get(scope, item.id);
      signal.throwIfAborted();
      if (current && state?.pendingDelete?.acknowledged && await contentDigest(current) === state.pendingDelete.contentDigest) {
        signal.throwIfAborted();
        await editorDrafts.remove(scope, current);
      }
    }
    signal.throwIfAborted();
    const remote = await cloudDraftApi.list(scope, signal);
    signal.throwIfAborted();
    let local = await editorDrafts.list(scope);
    for (const record of remote) {
      signal.throwIfAborted();
      if (local.some(item => item.cloud?.id === record.id && item.cloud.version === record.version)) continue;
      const source = local.find(item => item.source.sha256 === record.document.source_sha256)?.source ??
        await cloudDraftApi.source(scope.rootAssetId, signal);
      if (source.sha256 !== record.document.source_sha256 || source.width !== record.document.width ||
          source.height !== record.document.height) throw new Error("Cloud draft root mismatch");
      const files: { localUploadedReferenceFile: File | null; extraReferenceFile: File | null } =
        { localUploadedReferenceFile: null, extraReferenceFile: null };
      for (const attachment of record.document.attachments) {
        const blob = await cloudDraftApi.asset(attachment.asset_id, signal);
        const file = new File([blob], attachment.file_name, { type: blob.type, lastModified: attachment.last_modified });
        if (attachment.slot === "local") files.localUploadedReferenceFile = file;
        else files.extraReferenceFile = file;
      }
      signal.throwIfAborted();
      const references:ReferenceAttachment[]=[];
      for(const item of record.document.content.localReferences??[]) {
        const reference:ReferenceAttachment={key:item.key,assetId:item.assetId,usage:item.usage};
        if(item.fileName) {
          const blob=await cloudDraftApi.asset(item.assetId,signal);
          signal.throwIfAborted();
          reference.file=new File([blob],item.fileName,{type:blob.type,lastModified:item.lastModified});
        }
        references.push(reference);
      }
      const {localReferences:wireReferences,...wireContent}=record.document.content;
      const content = { ...wireContent, maskBinding: wireContent.maskBinding ?? undefined, ...files,
        ...(wireReferences!==undefined?{localReferences:references}:{}) };
      const digest = await contentDigest({ content });
      signal.throwIfAborted();
      await editorDrafts.importCloud(scope, source, content,
        { id: record.id, version: record.version, uploads: {}, syncedContentDigest: digest });
      local = await editorDrafts.list(scope);
    }
  });
}
