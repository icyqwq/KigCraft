import type { CloudState } from "./cloudTypes";
import { draftScopeKey, type DraftScope, type DraftSource, type EditorDraftContent, type EditorDraftRecord } from "./types";
const databaseName = "kigcraft-editor-drafts";
function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    let blocked = false;
    const request = indexedDB.open(databaseName, 1);
    request.onupgradeneeded = () => {
      const store = request.result.createObjectStore("drafts", { keyPath: "id" });
      store.createIndex("scope", "scope");
    };
    request.onerror = () => reject(request.error);
    request.onblocked = () => { blocked = true; reject(new Error("Draft database is blocked")); };
    request.onsuccess = () => {
      if (blocked) { request.result.close(); return; }
      request.result.onversionchange = () => request.result.close();
      resolve(request.result);
    };
  });
}
async function transaction<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore, done: (value: T) => void) => void): Promise<T> {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction("drafts", mode);
    let value: T;
    tx.oncomplete = () => { db.close(); resolve(value); };
    tx.onabort = tx.onerror = () => { db.close(); reject(tx.error ?? new Error("Draft write failed")); };
    try { action(tx.objectStore("drafts"), result => { value = result; }); }
    catch (error) { tx.abort(); db.close(); reject(error); }
  });
}
export const editorDrafts = {
  get(scope: DraftScope, id: string): Promise<EditorDraftRecord | undefined> {
    return transaction("readonly", (store, done) => {
      const request = store.get(id);
      request.onsuccess = () => done(request.result?.scope === draftScopeKey(scope) ? request.result : undefined);
    });
  },
  setCloud(scope: DraftScope, id: string, cloud: CloudState): Promise<boolean> {
    return transaction("readwrite", (store, done) => {
      const request = store.get(id);
      request.onsuccess = () => {
        const current = request.result as EditorDraftRecord | undefined;
        if (current?.scope !== draftScopeKey(scope)) { done(false); return; }
        store.put({ ...current, cloud }); done(true);
      };
    });
  },
  importCloud(scope: DraftScope, source: DraftSource, content: EditorDraftContent, cloud: CloudState): Promise<void> {
    return transaction("readwrite", (store, done) => {
      const record: EditorDraftRecord = { schemaVersion: content.localReferences === undefined ? 1 : 2, id: crypto.randomUUID(), scope: draftScopeKey(scope),
        version: 1, updatedAt: Date.now(), source, content, cloud: { ...cloud, syncedLocalVersion: 1 } };
      store.put(record); done(undefined);
    });
  },
  list(scope: DraftScope): Promise<EditorDraftRecord[]> {
    return transaction("readonly", (store, done) => {
      const request = store.index("scope").getAll(draftScopeKey(scope));
      request.onsuccess = () => done((request.result as EditorDraftRecord[]).sort((a, b) => b.updatedAt - a.updatedAt));
    });
  },
  /** Atomic compare-and-swap; a stale tab writes a new copy instead of overwriting. */
  save(scope: DraftScope, source: DraftSource, content: EditorDraftContent,
    previous?: Pick<EditorDraftRecord, "id" | "version">): Promise<EditorDraftRecord> {
    const scopeKey = draftScopeKey(scope);
    return transaction("readwrite", (store, done) => {
      const write = (current?: EditorDraftRecord) => {
        const matches = previous && current?.scope === scopeKey && current.version === previous.version;
        if (matches && current.schemaVersion === 2 && content.localReferences === undefined) throw new Error("Cannot downgrade a unified reference draft");
        const record: EditorDraftRecord = {
          schemaVersion: content.localReferences === undefined ? 1 : 2, id: matches ? previous.id : crypto.randomUUID(), scope: scopeKey,
          version: matches ? previous.version + 1 : 1, updatedAt: Math.max(Date.now(), (current?.updatedAt ?? 0) + 1),
          ...(previous && !matches ? { conflictOf: previous.id } : current?.conflictOf ? { conflictOf: current.conflictOf } : {}),
          source, content, ...(matches && current.cloud ? { cloud: current.cloud } : {}),
        };
        store.put(record); done(record);
      };
      if (!previous) write();
      else { const request = store.get(previous.id); request.onsuccess = () => write(request.result); }
    });
  },
  remove(scope: DraftScope, previous: Pick<EditorDraftRecord, "id" | "version">): Promise<boolean> {
    return transaction("readwrite", (store, done) => {
      const request = store.get(previous.id);
      request.onsuccess = () => {
        const current = request.result as EditorDraftRecord | undefined;
        if (current?.scope !== draftScopeKey(scope) || current.version !== previous.version) { done(false); return; }
        store.delete(previous.id); done(true);
      };
    });
  },
};
/**
 * Server images of AI output arrive watermarked; the server names the stored original they stand for, and that
 * original's hash is the source identity checkpoints and saves are bound to.
 */
export async function inspectServedSource(response: Response): Promise<DraftSource> {
  return inspectDraftSource(await response.blob(), response.headers.get("x-source-sha256") ?? undefined);
}
export async function inspectDraftSource(blob: Blob, identity?: string): Promise<DraftSource> {
  if (!blob.size || blob.size > 128 * 1024 * 1024) throw new Error("Image is too large for a local draft");
  const bitmap = await createImageBitmap(blob);
  const { width, height } = bitmap; bitmap.close();
  if (!width || !height || width > 16384 || height > 16384 || width * height > 67108864) throw new Error("Unsupported image dimensions");
  const hash = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  const sha256 = identity && /^[0-9a-f]{64}$/.test(identity) ? identity : Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, "0")).join("");
  return { blob, width, height, sha256 };
}
