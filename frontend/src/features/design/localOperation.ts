import { ReferenceUploader, type ReferenceAttachment } from "../references/attachments";
import { submitTurnSchema, type SubmitTurn } from "../../contracts/v2";
import type { EditorLocalGeneratePayload, EditorLocalReferenceOption } from "../editor/localGeneration";
import type { DraftSource } from "../editor/drafts/types";
import { activeStates, designApi, DesignApiError, type Operation } from "./api";

export type LocalTarget = { ownerId: string; branchId: string; baseRevisionId: string; rootAssetId: string;
  branchVersion: number; historical: boolean };
export type LocalTicket =
  { phase: "quoted"; payload: SubmitTurn; units: number; attempted: boolean } |
  { phase: "submitted"; operationId: string; branchId: string };
/** Freeze before quoting; persist before dispatch. Never replace an uncertain request with a new identity. */
export class LocalEditorOperation {
  ticket: LocalTicket | null = null;
  private busy = false;
  private uploader = new ReferenceUploader();
  readonly key: string;
  constructor(readonly target: LocalTarget, private storage: Storage = sessionStorage, private api = designApi) {
    this.key = "kig-v2-local-operation:" + JSON.stringify([target.ownerId, target.branchId, target.baseRevisionId, target.rootAssetId]);
  }
  load() {
    const saved = this.storage.getItem(this.key);
    if (!saved) return this.ticket = null;
    const record = JSON.parse(saved);
    if (record.schema !== 1 || record.ownerId !== this.target.ownerId) throw new Error("Unsupported local operation");
    if (record.ticket?.phase === "quoted") {
      const payload = submitTurnSchema.parse(record.ticket.payload);
      if (payload.base_revision_id !== this.target.baseRevisionId || !payload.prepared_edit ||
          payload.prepared_edit.root_source_asset_id !== this.target.rootAssetId ||
          !Number.isInteger(record.ticket.units) || record.ticket.units < 0 || typeof record.ticket.attempted !== "boolean") {
        throw new Error("Invalid local operation");
      }
      this.ticket = { ...record.ticket, payload };
    } else if (record.ticket?.phase === "submitted" && typeof record.ticket.operationId === "string" &&
        typeof record.ticket.branchId === "string") this.ticket = record.ticket;
    else throw new Error("Invalid local operation");
    return this.ticket;
  }
  private persist(ticket: LocalTicket) {
    this.storage.setItem(this.key, JSON.stringify({schema:1, ownerId:this.target.ownerId, ticket}));
    this.ticket = ticket;
    return ticket;
  }
  discardQuote() {
    if (this.busy || this.ticket?.phase !== "quoted" || this.ticket.attempted) return false;
    this.storage.removeItem(this.key); this.ticket = null; return true;
  }
  async prepare(input: EditorLocalGeneratePayload, source: Pick<DraftSource,"sha256"|"width"|"height">,
      references: EditorLocalReferenceOption[], signal: AbortSignal, onUploaded?: (item:ReferenceAttachment)=>void) {
    if (this.busy || this.load()) return this.ticket;
    // Detach mutable UI objects before any upload or await.
    const recipe = structuredClone(input.recipe);
    const selected = input.references ? [] : input.selectedReferenceKeys.map(key => {
      const option = references.find(item => item.key === key);
      if (!option) throw new Error("Selected reference unavailable");
      return {asset_id:key, usage:option.description ?? ""};
    });
    const items:ReferenceAttachment[] = input.references?.map(item=>({...item})) ?? [
      ...selected.map(item=>({key:item.asset_id,assetId:item.asset_id,usage:item.usage})),
      ...input.uploadedReferences.map((item,index)=>({key:"upload-"+index,usage:item.description,file:item.file})),
    ];
    const mode = input.localEditMode ?? "preserve";
    const note = input.editNote;
    const root = {...source};
    this.busy = true;
    signal = AbortSignal.any([signal, AbortSignal.timeout(60000)]);
    try {
      signal.throwIfAborted();
      const clean = await this.api.upload(input.baseImageBlob, this.target.ownerId, signal);
      signal.throwIfAborted();
      const mask = await this.api.upload(input.maskImageBlob, this.target.ownerId, signal);
      signal.throwIfAborted();
      const ready = await this.uploader.prepare(items, async file=>{
        signal.throwIfAborted();
        const asset = await this.api.upload(file,this.target.ownerId,signal);
        signal.throwIfAborted();
        return asset;
      },item=>{
        signal.throwIfAborted();
        onUploaded?.(item);
      });
      const attachments=ready.map(item=>({asset_id:item.assetId!,usage:item.usage}));
      signal.throwIfAborted();
      const request: Omit<SubmitTurn,"quote_id"> = {
        schema_version:1,client_request_id:crypto.randomUUID(),intent:"generate_or_clarify",
        text:note,base_revision_id:this.target.baseRevisionId,expected_branch_version:this.target.branchVersion,
        // The result never joins the conversation, so it is neither a continuation nor tied to the branch's version.
        continuation:null,inline_local:true,
        regenerate_revision_id:null,annotation_asset_id:null,mask_asset_id:mask.id,attachments,local_edit_mode:mode,
        prepared_edit:{schema_version:1,clean_asset_id:clean.id,root_source_asset_id:this.target.rootAssetId,
          root_sha256:root.sha256,width:root.width,height:root.height,recipe},
      };
      const quote = await this.api.quote(this.target.branchId,request,signal);
      signal.throwIfAborted();
      return this.persist({phase:"quoted",payload:submitTurnSchema.parse({...request,quote_id:quote.id}),
        units:quote.max_units,attempted:false});
    } finally { this.busy = false; }
  }
  clearFinished(operation: Operation) {
    if (this.busy || this.ticket?.phase !== "submitted" || this.ticket.operationId !== operation.id ||
        activeStates.has(operation.state) || operation.settled_units > 0) return false;
    this.storage.removeItem(this.key); this.ticket = null; return true;
  }
  acknowledgeFinished(operation: Operation) {
    if (this.busy || this.ticket?.phase !== "submitted" || this.ticket.operationId !== operation.id ||
        activeStates.has(operation.state)) return false;
    this.storage.removeItem(this.key); this.ticket = null; return true;
  }
  async submit(signal: AbortSignal) {
    if (this.busy) return this.ticket;
    const current = this.load();
    if (current?.phase !== "quoted") return current;
    this.busy = true;
    signal = AbortSignal.any([signal, AbortSignal.timeout(30000)]);
    try {
      signal.throwIfAborted();
      const attempted = {...current,attempted:true};
      this.persist(attempted); // A storage error prevents network dispatch.
      const operation = await this.api.submit(this.target.branchId,attempted.payload,signal);
      // Even if the component closed meanwhile, preserve a confirmed receipt for recovery.
      return this.persist({phase:"submitted",operationId:operation.id,branchId:operation.branch_id});
    } catch (error) {
      if (error instanceof DesignApiError && error.code !== "idempotency_conflict" && [400,403,404,409,422].includes(error.status)) {
        this.storage.removeItem(this.key); this.ticket = null;
      }
      throw error; // Network/5xx/401/abort keeps the exact request and quote.
    } finally { this.busy = false; }
  }
}
