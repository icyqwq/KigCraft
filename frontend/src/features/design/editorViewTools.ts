import type {EditorTool} from "../editor/components/EditorToolRail";
const alternateViews=new Set(["left_three_quarter","left_profile","right_three_quarter","right_profile","back"]);
export function editorViewTools(view:string,local:boolean):EditorTool[]{
 if(view==="front")return ["annotation","proportion","face","eyes","brows","mouth","liquify",...(local?["local-generate" as const]:[])];
 if(alternateViews.has(view))return ["annotation","liquify",...(local?["local-generate" as const]:[])];
 return ["annotation"];
}
