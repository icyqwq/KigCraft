import errorsZh from "./errors-zh-CN";
import errorsEn from "./errors-en";
import errorsJa from "./errors-ja";
import {editorCheckpointSchema,type EditorSaveRequest} from "./editorSaveContracts";
import { z } from "zod";
import { operationStateSchema, type SubmitTurn } from "../../contracts/v2";

export const designSchema = z.object({
  id: z.string(), title: z.string(), last_branch_id: z.string(),
  pinned: z.boolean(), metadata_version: z.number(), updated_at: z.string(),
  deleted_at:z.string().nullable().optional(),archived_at:z.string().nullable().optional(),last_activity_at:z.string().optional(),
});
const catalogItemSchema=designSchema.extend({
  thumbnail_url:z.string().nullable().optional(),operation_state:operationStateSchema.nullable().optional(),has_cloud_draft:z.boolean().optional(),
  match:z.object({message_id:z.string(),branch_id:z.string(),text:z.string()}).nullable().optional(),
});
const catalogPageSchema=z.object({items:z.array(catalogItemSchema),next_cursor:z.string().nullable().optional().default(null)});
const messageSchema = z.object({
  history_sequence:z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional(), branch_id:z.string().optional(), can_edit:z.boolean().optional(),
  id: z.string(), role: z.string(), status: z.string(), created_at: z.string(),
  operation_id: z.string().nullable(),
  content_parts: z.array(z.object({
    type: z.string(), text: z.string().optional(),
    items: z.array(z.object({ asset_id: z.string(), usage: z.string(), number:z.number().int().positive().optional() })).optional(),
    annotations:z.array(z.object({annotation_id:z.string(),number:z.number().int().positive(),note:z.string(),text:z.string()})).optional(),
  })),
});
export const revisionSchema = z.object({
  width:z.number().int().positive().optional(),height:z.number().int().positive().optional(),
  history_sequence:z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional(),
  number: z.number().int().positive().max(Number.MAX_SAFE_INTEGER).optional(),
  id: z.string(), branch_id: z.string(), parent_revision_id: z.string().nullable(),
  asset_id: z.string(), root_source_asset_id: z.string(), root_image_url: z.string(),
  image_url: z.string(), operation_id: z.string().nullable(), source_kind: z.string(),
  view: z.string(), created_at: z.string(), recipe: z.record(z.unknown()).nullable(),
  can_continue: z.boolean().optional(), can_regenerate: z.boolean().optional(),
  // Older servers omit it; a missing value means a finished head-shell photo.
  image_kind: z.enum(["head_shell","reference"]).optional(),
});
const revisionPageSchema=z.object({
  items:z.array(revisionSchema),next_cursor:z.string().nullable(),
  watermark:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),total:z.number().int().nonnegative(),
});
export const candidatePageSchema=z.object({
  group:z.object({id:z.string(),requested_operation_kind:z.enum(["generate","image_edit","local_edit"])}).nullable(),
  items:z.array(revisionSchema),next_cursor:z.string().nullable(),
  watermark:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),total:z.number().int().nonnegative(),
});
export const editorSaveResultSchema=z.object({disposition:z.enum(["revision","checkpoint","unchanged"]),checkpoint:editorCheckpointSchema,revision:revisionSchema}).superRefine((result,ctx)=>{
  const {checkpoint,revision}=result;
  if(checkpoint.revision_id!==revision.id||checkpoint.clean_asset_id!==revision.asset_id||checkpoint.document.root.assetId!==revision.root_source_asset_id||
    (result.disposition==="revision"&&checkpoint.branch_id!==revision.branch_id))
    ctx.addIssue({code:"custom",message:"Editor save receipt mismatch"});
});
export type EditorSaveResult=z.infer<typeof editorSaveResultSchema>;
const operationSchema = z.object({
  id: z.string(), branch_id: z.string(), kind: z.string(), state: operationStateSchema,
  base_revision_id: z.string().nullable(), reserved_units: z.number(),
  settled_units: z.number(), error_code: z.string().nullable(),
  /** The editor's local-generation result (a bare asset); present once the operation succeeded. */
  result_asset_id: z.string().optional(),
});
export const historyPageSchema=z.object({
  items:z.array(z.discriminatedUnion("type",[
    z.object({type:z.literal("message"),message:messageSchema}),
    z.object({type:z.literal("revision"),revision:revisionSchema}),
  ])),
  next_cursor:z.string().nullable(),watermark:z.number().int().nonnegative().max(Number.MAX_SAFE_INTEGER),ancestry_incomplete:z.boolean(),
});
export type HistoryPage=z.infer<typeof historyPageSchema>;
const questionSchema=z.object({operation_id:z.string(),version:z.number().int().positive(),state:z.enum(["open","expired","answered"]),
  expires_at:z.string(),messages:z.array(z.object({message_id:z.string(),text:z.string()})),answered_by:z.string().nullable()});
export type Question=z.infer<typeof questionSchema>;
export const snapshotSchema = designSchema.extend({
  legacy_imported:z.boolean().optional(),
  questions:z.array(questionSchema).optional(),
  history:historyPageSchema.optional(),current_revision:revisionSchema.nullable().optional(),
  snapshot_sequence: z.number(),
  branch: z.object({ id: z.string(), state_version: z.number(), current_revision_id: z.string().nullable() }),
  branches: z.array(z.object({id:z.string(),parent_branch_id:z.string().nullable(),origin_revision_id:z.string().nullable()})).optional(),
  design_active_operation: operationSchema.nullable().optional(),
  messages: z.array(messageSchema), revisions: z.array(revisionSchema), operations: z.array(operationSchema),
});
export const capabilitiesSchema = z.object({
  enabled: z.boolean(), new_operations: z.boolean(), generation: z.boolean(),
  discussion: z.boolean(), history_fork: z.boolean(), viewsets: z.boolean(), local_edits: z.boolean().optional().default(false), max_input_images: z.number().int().positive().optional().default(8),
});
const quotaSchema = z.object({
  available: z.number().nullable(), reserved: z.number().nullable(), balance: z.number().nullable(), status: z.string(),
  reset_at: z.string().optional(), exempt: z.boolean().optional(),
  entries: z.array(z.object({
    id: z.string(), kind: z.string(), balance_delta: z.number(), reserved_delta: z.number(),
    created_at: z.string(), operation_id: z.string().nullable(),
  })).default([]),
});
const assetSchema = z.object({
  id: z.string(), image_url: z.string(), status: z.string(), width: z.number(), height: z.number(),
});
const previewSchema = z.object({ text:z.string(), base_image_url:z.string().nullable(),
  references:z.array(z.object({image_url:z.string(),usage:z.string(),number:z.number().int().positive().optional()})) });
const quoteSchema = z.object({ id: z.string(), max_units: z.number(), expires_at: z.string(), request_preview:previewSchema.optional() });
export type RequestPreview = z.infer<typeof previewSchema>;
export type HistoryTarget = {id:string;kind:"continue"|"regenerate";revisionId:string;imageUrl:string;branchVersion:number}
  | {id:string;kind:"message";messageId:string;revisionId:string|null;imageUrl:string|null;branchVersion:number};
const messageEditSchema=z.object({message_id:z.string(),source_revision_id:z.string().nullable(),branch_version:z.number().int().nonnegative(),text:z.string(),base_image_url:z.string().nullable(),
  references:z.array(z.object({asset_id:z.string(),usage:z.string(),number:z.number().int().positive(),image_url:z.string()})),
  reserved_images:z.number().int().nonnegative(),target_kind:z.enum(["reference","head_shell","four_view"]).nullable().optional(),local_edit_mode:z.enum(["preserve","lock_outside"]).nullable(),
  annotations:z.array(z.object({annotation_id:z.string(),number:z.number().int().positive(),note:z.string(),text:z.string()})),
});
export type MessageEditPreparation=z.infer<typeof messageEditSchema>;
const questionPreparationSchema=messageEditSchema.merge(questionSchema).extend({branch_id:z.string()});

export type Design = z.infer<typeof designSchema>;
export type Snapshot = z.infer<typeof snapshotSchema>;
export type Revision = z.infer<typeof revisionSchema>;
export const imageKindOf=(revision:{image_kind?:string})=>revision.image_kind==="reference"?"reference" as const:"head_shell" as const;
export type Operation = z.infer<typeof operationSchema>;
export type UploadedAsset = z.infer<typeof assetSchema>;

export class DesignApiError extends Error {
  constructor(public code: string, public status: number) { super(code); }
}
export async function call<S extends z.ZodTypeAny>(path: string, schema: S, init: RequestInit = {}): Promise<z.output<S>> {
  const response = await fetch("/api/v2" + path, {
    ...init, credentials: "same-origin",
    headers: init.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...init.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new DesignApiError(body.code ?? body.detail?.code ?? "request_failed", response.status);
  }
  return schema.parse(await response.json());
}
export const designApi = {
  capabilities: () => call("/capabilities", capabilitiesSchema),
  list: (q: string,status="active",cursor:string|null=null,signal?:AbortSignal) => call("/designs?"+new URLSearchParams({q,status,...(cursor?{cursor}:{})}),catalogPageSchema,{signal}),
  update:(design:Design,changes:{pinned?:boolean;archived?:boolean})=>call("/designs/"+encodeURIComponent(design.id),designSchema,{method:"PATCH",body:JSON.stringify({...changes,expected_metadata_version:design.metadata_version})}),
  snapshot: (id: string, branch?: string) => call("/designs/" + encodeURIComponent(id) + (branch ? "?branch_id=" + encodeURIComponent(branch) : ""), snapshotSchema),
  history: (branch:string,cursor:string|null,signal?:AbortSignal,messageId?:string) => call("/branches/"+encodeURIComponent(branch)+"/history?"+new URLSearchParams(cursor?{cursor}:messageId?{message_id:messageId}:{}),historyPageSchema,{signal}),
  prepareMessageEdit:(branch:string,message:string,signal?:AbortSignal)=>call("/branches/"+encodeURIComponent(branch)+"/messages/"+encodeURIComponent(message)+"/edit",messageEditSchema,{signal}),
  candidates:(revision:string,cursor:string|null,signal?:AbortSignal)=>call("/revisions/"+encodeURIComponent(revision)+"/candidates"+(cursor?"?cursor="+encodeURIComponent(cursor):""),candidatePageSchema,{signal}),
  revision:(design:string,revision:string,signal?:AbortSignal)=>call("/designs/"+encodeURIComponent(design)+"/revisions/"+encodeURIComponent(revision),revisionSchema.refine(item=>item.id===revision),{signal}),
  revisions:(design:string,cursor:string|null,signal?:AbortSignal)=>call("/designs/"+encodeURIComponent(design)+"/revisions?"+new URLSearchParams({scope:"design",...(cursor?{cursor}:{})}),revisionPageSchema,{signal}),
  lifecycle:(id:string)=>call("/designs/"+encodeURIComponent(id)+"/lifecycle",designSchema),
  /** Permanent: the server removes the design, its conversation, versions and images. */
  remove:(design:Design)=>call("/designs/"+encodeURIComponent(design.id),z.object({deleted:z.literal(true)}),{method:"DELETE",body:JSON.stringify({expected_metadata_version:design.metadata_version})}),
  prepareQuestion:(id:string,signal?:AbortSignal)=>call("/operations/"+encodeURIComponent(id)+"/question",questionPreparationSchema,{signal}),
  quota: () => call("/account/quota", quotaSchema),
  create: (key: string, title: string) => call("/designs", designSchema, {
    method: "POST", body: JSON.stringify({ client_request_id: key, title }),
  }),
  rename: (design: Design, title: string) => call("/designs/" + design.id, designSchema, {
    method: "PATCH", body: JSON.stringify({ title, expected_metadata_version: design.metadata_version }),
  }),
  upload: (file: Blob, ownerId?: string, signal?: AbortSignal) => {
    const body = new FormData();
    body.append("file", file, "reference.png");
    return call("/assets/uploads" + (ownerId ? "?expected_owner_id=" + encodeURIComponent(ownerId) : ""), assetSchema, { method: "POST", body, signal });
  },
  quote: (branch: string, payload: Omit<SubmitTurn, "quote_id">, signal?: AbortSignal) => call("/branches/" + branch + "/quotes", quoteSchema, {
    method: "POST", body: JSON.stringify(payload), signal,
  }),
  submit: (branch: string, payload: SubmitTurn, signal?: AbortSignal) => call(payload.answer_to?"/operations/"+encodeURIComponent(payload.answer_to.operation_id)+"/answers":"/branches/" + branch + "/turns", operationSchema, {
    method: "POST", body: JSON.stringify(payload), signal,
  }),
  cancel: (id: string) => call("/operations/" + id + "/cancel", z.object({ state: operationStateSchema }), { method: "POST" }),
  editorCheckpoint:(branch:string,base:string,signal?:AbortSignal)=>call("/branches/"+encodeURIComponent(branch)+"/editor-checkpoint?base_revision_id="+encodeURIComponent(base),z.object({checkpoint:editorCheckpointSchema.nullable()}),{signal}),
  saveEditor:(branch:string,payload:EditorSaveRequest,signal?:AbortSignal)=>call("/branches/"+encodeURIComponent(branch)+"/editor-saves",editorSaveResultSchema,{method:"POST",body:JSON.stringify(payload),signal}),
  saveManual: (branch: string, payload: {
    client_request_id: string; base_revision_id: string; expected_branch_version: number;
    clean_asset_id: string; recipe: object; continuation?: {source_revision_id:string;boundary_message_id:null};
  }) => call("/branches/" + branch + "/manual-revisions", revisionSchema, { method: "POST", body: JSON.stringify(payload) }),
};
export const activeStates = new Set(["queued", "reviewing", "running", "reconciling", "publishing", "cancelling"]);
export function errorText(error: unknown, language = "zh-CN"): string {
  const messages: Record<keyof typeof errorsZh, string> = language.startsWith("en") ? errorsEn : language.startsWith("ja") ? errorsJa : errorsZh;
  if (error instanceof DesignApiError && error.status === 401) return messages.sign_in_required;
  const code = error instanceof DesignApiError ? error.code : typeof error === "string" ? error : "";
  return Object.prototype.hasOwnProperty.call(messages,code) ? messages[code as keyof typeof messages] : messages.request_failed;
}
