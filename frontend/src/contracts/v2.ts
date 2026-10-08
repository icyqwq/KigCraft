import { z } from "zod";

const identity = z.string().min(1).max(100);
const nullableIdentity = identity.nullable().optional().default(null);

export const operationKindSchema = z.enum([
  "generate", "image_edit", "local_edit", "regenerate", "viewset", "discussion",
]);
export const operationStateSchema = z.enum([
  "queued", "reviewing", "awaiting_input", "running", "reconciling",
  "publishing", "cancelling", "succeeded", "partial_success", "failed", "cancelled",
]);
export const referenceAttachmentSchema = z.object({
  asset_id: identity,
  usage: z.string().max(2000),
}).strict();

export const continuationDraftSchema = z.object({
  source_revision_id: nullableIdentity,
  boundary_message_id: nullableIdentity,
}).strict().refine(value=>Boolean(value.source_revision_id||value.boundary_message_id),"A revision or message boundary is required");

export const submitTurnSchema = z.object({
  answer_to:z.object({operation_id:identity,version:z.number().int().positive()}).strict().nullable().optional(),
  schema_version: z.literal(1).default(1),
  client_request_id: z.string().min(8).max(100),
  intent: z.enum(["generate_or_clarify", "discussion_only"]),
  text: z.string().max(16000),
  base_revision_id: nullableIdentity,
  expected_branch_version: z.number().int().min(0),
  continuation: continuationDraftSchema.nullable().default(null),
  regenerate_revision_id: nullableIdentity,
  attachments: z.array(referenceAttachmentSchema).default([]),
  annotation_asset_id: nullableIdentity,
  mask_asset_id: nullableIdentity,
  local_edit_mode: z.enum(["preserve", "lock_outside"]).nullable().optional(),
  prepared_edit: z.object({
    schema_version: z.literal(1).default(1), clean_asset_id: identity, root_source_asset_id: identity,
    root_sha256: z.string().regex(/^[0-9a-f]{64}$/), width: z.number().int().positive().max(16384),
    height: z.number().int().positive().max(16384), recipe: z.record(z.unknown()),
  }).strict().nullable().optional(),
  quote_id: nullableIdentity,
  /** The editor's own local generation: no message, no version, a throw-away session. */
  inline_local: z.boolean().optional(),
  /** What this turn should produce; unset leaves it to the conversation (follow-ups keep the base image kind). */
  target_kind: z.enum(["reference", "head_shell", "four_view"]).nullable().optional(),
}).strict().superRefine((value, context) => {
  const invalid = (message: string) => context.addIssue({ code: "custom", message });
  if(value.answer_to&&(value.continuation||value.regenerate_revision_id||value.local_edit_mode||value.prepared_edit||value.annotation_asset_id||value.mask_asset_id||value.intent!=="generate_or_clarify"))invalid("Answers retain the question original input bindings");
  if (!value.text.trim() && !value.attachments.length && !value.regenerate_revision_id) invalid("Text or a reference is required");
  if (value.intent !== "discussion_only" && !value.quote_id) invalid("Generation requires a quote");
  if (value.mask_asset_id && !value.base_revision_id) invalid("A local mask requires its base revision");
  if (value.local_edit_mode && (!value.mask_asset_id || value.intent === "discussion_only")) invalid("Local edit mode requires an image edit mask");
  if (value.prepared_edit && (!value.base_revision_id || !value.mask_asset_id)) invalid("Prepared edit requires its base and mask");
  if (value.inline_local && !(value.local_edit_mode && value.prepared_edit && value.mask_asset_id)) invalid("Inline local generation needs the editor's prepared edit and mask");
  if (value.regenerate_revision_id && (value.local_edit_mode || value.prepared_edit || value.text || value.attachments.length || value.base_revision_id ||
      value.continuation || value.annotation_asset_id || value.mask_asset_id || value.intent === "discussion_only")) {
    invalid("Regeneration uses only the original immutable request");
  }
  if(value.continuation?.boundary_message_id&&(value.local_edit_mode||value.prepared_edit||value.annotation_asset_id||value.mask_asset_id))invalid("Message editing retains original annotation and local edit bindings");
  if(value.target_kind&&(value.answer_to||value.regenerate_revision_id||value.local_edit_mode||value.prepared_edit||value.mask_asset_id||value.annotation_asset_id||value.intent==="discussion_only"))invalid("A target kind belongs to a plain generation turn");
  if (new Set(value.attachments.map((item) => item.asset_id)).size !== value.attachments.length) {
    invalid("Duplicate reference identity");
  }
});

export type SubmitTurn = z.infer<typeof submitTurnSchema>;
export type ReferenceAttachment = z.infer<typeof referenceAttachmentSchema>;
export type ContinuationDraft = z.infer<typeof continuationDraftSchema>;
export type OperationKind = z.infer<typeof operationKindSchema>;
export type OperationState = z.infer<typeof operationStateSchema>;
