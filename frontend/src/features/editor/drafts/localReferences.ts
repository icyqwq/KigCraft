import type { ReferenceAttachment, ReferenceOption } from "../../references/attachments";
import type { EditorDraftContent } from "./types";
export function restoreLocalReferences(draft:EditorDraftContent|undefined, options:ReferenceOption[]):ReferenceAttachment[] {
  if(draft?.localReferences) return draft.localReferences.map(item=>({...item}));
  const selected=(draft?.selectedLocalReferenceKeys??[]).map(key=>({key,assetId:key,usage:options.find(item=>item.key===key)?.description??""}));
  return [...selected,...(draft?.localUploadedReferenceFile?[{key:"legacy-local-upload",file:draft.localUploadedReferenceFile,
    usage:draft.localUploadedReferenceDescription}]:[])];
}
