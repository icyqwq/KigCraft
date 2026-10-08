import {z} from "zod";
import {call} from "./api";
export const legacyPageSchema=z.object({
 items:z.array(z.object({
  id:z.string(),source_kind:z.enum(["job","output","album"]),source_id:z.string(),
  provider:z.object({provider:z.string().nullable(),analysis_provider:z.string().nullable(),analysis_model:z.string().nullable(),
   image_provider:z.string().nullable(),image_model:z.string().nullable()}),
  legacy_execution:z.string(),legacy_selection:z.string(),view:z.string(),recipe_state:z.string(),
  recipe_replayed:z.literal(false),issues:z.array(z.string()),
 })),
 next_cursor:z.string().nullable(),
});
export function readLegacySources(identity:string,cursor:string|null,signal?:AbortSignal,scope:"revisions"|"designs"="revisions"){
 return call("/"+scope+"/"+encodeURIComponent(identity)+"/legacy"+(cursor?"?cursor="+encodeURIComponent(cursor):""),legacyPageSchema,{signal});
}
