import {useEditorCopy,type EditorCopyKey} from "../useEditorCopy";
import { EditorBodySkeleton } from "../../design/Skeletons";
import type {ProjectCapture} from "../io/project";
import {editorStateFromDocument,type SavedEditorDocument} from "../core/editorDocument";
import {normalizeEditRecipe} from "../deformation/recipe";
import { CloudDraftSync, recoverCloudDrafts } from "./sync";
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { EditorWorkspace, type EditorWorkspaceHandle, type EditorWorkspaceProps } from "../EditorWorkspace";
import { compactRecipeAnnotations, type EditRecipe } from "../deformation/recipe";
import { editorDrafts, inspectServedSource } from "./repository";
import { DraftSession } from "./session";
import { draftScopeKey, type DraftScope, type DraftSource, type EditorDraftContent, type EditorDraftRecord } from "./types";

export type PersistentEditorHandle = {
  captureProject: () => ProjectCapture;
  captureForAdoption: () => Promise<ProjectCapture & {clean:Blob}>;
  markReferenceUploaded: (key:string,file:File,assetId:string)=>void;
  markReferenceUploadFailure: (key:string,file:File)=>void;
  source: () => Pick<DraftSource, "sha256" | "width" | "height">;
  flush: () => Promise<void>;
  /** Runs the editor save control (exports the image and calls onSave). */
  requestSave: () => Promise<void>;
  clearSavedDocument: (document:SavedEditorDocument) => Promise<void>;
  clearSavedRecipe: (recipe: EditRecipe) => Promise<void>;
};
type Props = EditorWorkspaceProps & { scope: DraftScope; sourceUrl: string; cloudSync?: boolean; cloudDraftId?: string; savedDocument?:SavedEditorDocument };
const canonical=(value:unknown):unknown=>Array.isArray(value)?value.map(canonical):value&&typeof value==="object"
  ?Object.fromEntries(Object.entries(value).filter(([,item])=>item!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,canonical(item)])):value;
type Loaded = { source: DraftSource; url: string; record?: EditorDraftRecord; session: DraftSession };
/** Caller keys this component by owner + branch + base. Server current-pointer updates never replace the base. */
/** Routine autosave progress is not shown; only problems and conflicts are. */
const routine: ReadonlySet<string> = new Set(["synced", "savedLocally", "savingDraft", "syncingDraft", "cloudCleared"]);

export const PersistentEditor = forwardRef<PersistentEditorHandle, Props>(function PersistentEditor(
  { scope, sourceUrl, cloudSync = false, cloudDraftId, savedDocument, onDraftChange, ...props }, ref,
) {
  const {c,language}=useEditorCopy();
  const fallbackDraft=useMemo<EditorDraftContent|undefined>(()=>savedDocument?{
    localEditNote:"",selectedLocalReferenceKeys:[],localUploadedReferenceFile:null,
    localUploadedReferenceDescription:"",extraReferenceFile:null,extraReferenceDescription:"",
    ...props.initialDraft,...editorStateFromDocument(savedDocument),
  }:undefined,[savedDocument,props.initialDraft]);
  const stateKey=(content:EditorDraftContent)=>JSON.stringify(canonical({recipe:normalizeEditRecipe(content.recipe),maskStrokes:content.maskStrokes,maskBinding:content.maskBinding??null}));
  const [loaded, setLoaded] = useState<Loaded>();
  const [error, setError] = useState<EditorCopyKey|"">("");
  const [status, setStatus] = useState<EditorCopyKey|"">("");
  const [cloudStatus, setCloudStatus] = useState<EditorCopyKey|"">("");
  const cloud = useRef<CloudDraftSync | undefined>(undefined);
  const cloudTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const syncCloud = useCallback(async (id: string) => {
    const service = cloud.current;
    const generation = counter.current;
    if (!service) return;
    if (live.current) setCloudStatus("syncingDraft");
    try {
      const state = await service.sync(id);
      const current = loadedRef.current?.session.record;
      if (live.current && generation === counter.current && state?.pendingDelete?.acknowledged) setCloudStatus("cloudCleared");
      else if (live.current && generation === counter.current && current?.id === id && state?.syncedLocalVersion === current.version) {
        setCloudStatus(state.conflictCopy ? "cloudConflict" : "synced");
      }
    } catch {
      if (live.current) setCloudStatus("cloudError");
    }
  }, []);
  const [attempt, setAttempt] = useState(0);
  const editor = useRef<EditorWorkspaceHandle>(null);
  const latest = useRef<EditorDraftContent | undefined>(undefined);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const live = useRef(true);
  const loadedRef = useRef<Loaded | undefined>(undefined);
  const counter = useRef(0);
  const scopeKey = draftScopeKey(scope);
  const persist = useCallback(async (content: EditorDraftContent) => {
    const current = loadedRef.current;
    if (!current) return;
    const request = ++counter.current;
    if (live.current) setStatus("savingDraft");
    try {
      const record = await current.session.save(content);
      if (cloud.current && live.current) {
        clearTimeout(cloudTimer.current);
        cloudTimer.current = setTimeout(() => { cloudTimer.current = undefined; void syncCloud(record.id); }, 1200);
      }
      if (live.current && request === counter.current) {
        setError(""); setStatus(record.conflictOf ? "conflictCopy" : "savedLocally");
      }
    } catch (failure) {
      if (live.current && request === counter.current) { setStatus(""); setError("draftSaveError"); }
      throw failure;
    }
  }, []);
  const changed = useCallback((content: EditorDraftContent) => {
    latest.current = content; ++counter.current;
    if (cloud.current) setCloudStatus("waitingSync");
    onDraftChange?.(content);
    clearTimeout(timer.current);
    setStatus("savingDraft");
    timer.current = setTimeout(() => { timer.current = undefined; void persist(content).catch(() => {}); }, 250);
  }, [onDraftChange, persist]);
  const flush = useCallback(async () => {
    clearTimeout(timer.current); timer.current = undefined;
    const content = editor.current?.captureDraft() ?? latest.current;
    if (content) { latest.current = content; await persist(content); }
    await loadedRef.current?.session.flush();
  }, [persist]);
  useImperativeHandle(ref, () => ({
    flush,
    requestSave: async () => { await editor.current?.save(); },
    captureProject(){
      const current=loadedRef.current,content=editor.current?.captureProjectDraft();
      if(!current||!content)throw new Error("Editor source unavailable");
      return structuredClone({source:current.source,content,previous:savedDocument});
    },
    async captureForAdoption(){
      const current=loadedRef.current,workspace=editor.current;
      if(!current||!workspace)throw new Error("Editor source unavailable");
      const source=structuredClone(current.source),previous=structuredClone(savedDocument);
      const exported=await workspace.captureForAdoption();
      if(loadedRef.current!==current||!exported.draftSnapshot)throw new Error("Editor source changed");
      return {source,previous,content:exported.draftSnapshot,clean:exported.imageBlob};
    },
    markReferenceUploaded(key,file,assetId) { editor.current?.markReferenceUploaded(key,file,assetId); },
    markReferenceUploadFailure(key,file) { editor.current?.markReferenceUploadFailure(key,file); },
    source() {
      const source = loadedRef.current?.source;
      if (!source) throw new Error("Editor source unavailable");
      return {sha256:source.sha256,width:source.width,height:source.height};
    },
    async clearSavedDocument(document) {
      await flush();
      const content=latest.current,state=editorStateFromDocument(document);
      if(!content||stateKey(content)!==stateKey({...content,...state})||content.localEditNote||content.selectedLocalReferenceKeys.length||content.localReferences?.length||
        content.localUploadedReferenceFile||content.extraReferenceFile||content.localUploadedReferenceDescription||content.extraReferenceDescription)return;
      const current=loadedRef.current,record=current?.session.record,generation=counter.current;
      if(!live.current||!current||!record)return;
      clearTimeout(cloudTimer.current);
      if(cloud.current){try{if(!await cloud.current.remove(record))return;}catch{return;}}
      if(!live.current||loadedRef.current!==current||counter.current!==generation)return;
      if(!await current.session.discard(record))return;
      if(!live.current||loadedRef.current!==current||counter.current!==generation)return;
      latest.current=undefined;clearTimeout(timer.current);timer.current=undefined;
    },
    async clearSavedRecipe(recipe) {
      await flush();
      const content = latest.current;
      // A late save acknowledgement must not erase edits made during PNG export or pending local AI input.
      if (!content || JSON.stringify(compactRecipeAnnotations(content.recipe)) !== JSON.stringify(recipe) ||
          content.maskStrokes.length || content.localEditNote || content.selectedLocalReferenceKeys.length || content.localReferences?.length ||
          content.localUploadedReferenceFile || content.extraReferenceFile ||
          content.localUploadedReferenceDescription || content.extraReferenceDescription) return;
      const current = loadedRef.current, record = current?.session.record, generation = counter.current;
      if (!live.current || !current || !record) return;
      clearTimeout(cloudTimer.current);
      if (cloud.current) {
        try { if (!await cloud.current.remove(record)) return; }
        catch { return; }
      }
      if (!live.current || loadedRef.current !== current || counter.current !== generation) return;
      if (!await current.session.discard(record)) return;
      if (!live.current || loadedRef.current !== current || counter.current !== generation) return;
      latest.current = undefined; clearTimeout(timer.current); timer.current = undefined;
    },
  }), [flush,savedDocument]);
  useEffect(() => {
    live.current = true;
    let cancelled = false;
    const abort = new AbortController();
    cloud.current = cloudSync ? new CloudDraftSync(scope, abort.signal) : undefined;
    let objectUrl: string | undefined;
    const initialize = async () => {
      setError("");
      if (cloudSync) {
        try { await recoverCloudDrafts(scope, abort.signal); }
        catch { if (!cancelled) setCloudStatus("cloudReadError"); }
      }
      const allRecords = await editorDrafts.list(scope);
      const records = cloudDraftId ? allRecords.filter(record=>record.cloud?.id===cloudDraftId) : allRecords;
      if(cloudDraftId&&!records.length)throw new Error("Requested draft unavailable");
      if (cancelled) return;
      // Several saved copies (two tabs, an older visit) are never put to the user: the newest one is simply the one in use.
      const record = records[0];
      if (record && ![1,2].includes(record.schemaVersion)) throw new Error("Unknown draft schema");
      let source = record?.source;
      if (!source) {
        const response = await fetch(sourceUrl, { credentials: "same-origin", signal: abort.signal });
        if (!response.ok) throw new Error("Source unavailable");
        source = await inspectServedSource(response);
      }
      if (savedDocument && (source.sha256!==savedDocument.root.sha256||source.width!==savedDocument.root.width||source.height!==savedDocument.root.height)) throw new Error("Checkpoint source mismatch");
      if (cancelled) return;
      objectUrl = URL.createObjectURL(source.blob);
      const value = { source, url: objectUrl, record, session: new DraftSession(scope, source, record) };
      loadedRef.current = value; setLoaded(value);
    };
    void initialize().catch(() => { if (!cancelled) setError("draftOpenError"); });
    return () => {
      cancelled = true; live.current = false; abort.abort(); clearTimeout(timer.current); clearTimeout(cloudTimer.current);
      // Start the final write now; async completion remains bound to this session's original owner/base.
      if (latest.current && loadedRef.current) void loadedRef.current.session.save(latest.current).catch(() => {});
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [scopeKey, sourceUrl, attempt, cloudSync, cloudDraftId]);
  useEffect(() => () => { if (loaded) URL.revokeObjectURL(loaded.url); }, [loaded]);
  useEffect(() => {
    const save = () => { if (latest.current) { clearTimeout(timer.current); timer.current = undefined; void persist(latest.current).catch(() => {}); } };
    const online = () => { const record = loadedRef.current?.session.record; if (record) void syncCloud(record.id); };
    window.addEventListener("online", online);
    const visibility = () => { if (document.visibilityState === "hidden") save(); };
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (timer.current || loadedRef.current?.session.dirty) { event.preventDefault(); event.returnValue = ""; }
    };
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", save);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", save);
      window.removeEventListener("online", online);
      window.removeEventListener("beforeunload", beforeUnload);
    };
  }, [persist]);
  return <>
    {error ? <p role="alert" data-testid="editor-draft-error">{error?c(error): ""} <button onClick={() => loaded ? void flush().catch(() => {}) : setAttempt(value => value + 1)}>{c("retry")}</button></p> : null}
    {!loaded && !error ? <EditorBodySkeleton /> : null}
    {cloudSync && cloudStatus && !routine.has(cloudStatus) ? <p role="status" data-testid="editor-cloud-status">{cloudStatus?c(cloudStatus): ""}
      <button onClick={() => { const record = loadedRef.current?.session.record; if (record) void syncCloud(record.id); }}>{c("syncDraft")}</button>
    </p> : null}
    {loaded ? <>
      <p role="status" aria-live="polite" data-testid="editor-draft-status">{status && !routine.has(status) ? c(status) : ""}</p>
      <EditorWorkspace {...props} ref={editor} imageUrl={loaded.url} imageSourceKey={scope.rootAssetId}
        imageWidth={loaded.source.width} imageHeight={loaded.source.height}
        recipe={loaded.record?.content.recipe ?? fallbackDraft?.recipe ?? props.recipe}
        initialDraft={loaded.record?.content ?? fallbackDraft ?? props.initialDraft} onDraftChange={changed} />
    </> : null}
  </>;
});
