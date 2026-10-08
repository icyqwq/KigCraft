import {z} from "zod";
import {editorDocumentSchema} from "../editor/core/documentSchema";
export {editorDocumentSchema} from "../editor/core/documentSchema";
export const editorCheckpointSchema=z.object({
  id:z.string(),version:z.number().int().positive(),branch_id:z.string(),revision_id:z.string(),clean_asset_id:z.string(),
  document:editorDocumentSchema,created_at:z.string(),
}).superRefine((checkpoint,ctx)=>{
  if(checkpoint.document.baseRevision!==checkpoint.revision_id)
    ctx.addIssue({code:"custom",message:"Checkpoint revision mismatch"});
});
export type EditorCheckpoint=z.infer<typeof editorCheckpointSchema>;
export const editorSaveRequestSchema=z.object({
  client_request_id:z.string().min(8),expected_branch_version:z.number().int().nonnegative(),
  expected_checkpoint_id:z.string().nullable(),clean_asset_id:z.string(),document:editorDocumentSchema,
  continuation:z.object({source_revision_id:z.string(),boundary_message_id:z.null()}).optional(),
}).strict();
export type EditorSaveRequest=z.infer<typeof editorSaveRequestSchema>;
