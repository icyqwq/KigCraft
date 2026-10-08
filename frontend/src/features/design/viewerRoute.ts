export type ViewerLocation={origin:string;selected:string;comparison:string|null;allVersions:boolean};
const fields=["viewer","image","compare","versions"];
const id=(value:string|null)=>value!==null&&/^[a-zA-Z0-9_-]{1,100}$/.test(value);
export function readViewer(search:URLSearchParams):ViewerLocation|null{
 const origin=search.get("viewer"),selected=search.get("image")??origin,comparison=search.get("compare");
 const mode=search.get("versions");
 if(!id(origin)||!id(selected)||(comparison!==null&&!id(comparison))||(mode!==null&&mode!=="all"))return null;
 return {origin:origin!,selected:selected!,comparison:comparison===selected?null:comparison,allVersions:mode==="all"};
}
export function viewerSearch(search:URLSearchParams,value:ViewerLocation|null){
 const result=new URLSearchParams(search);
 for(const field of fields)result.delete(field);
 if(value){
  result.set("viewer",value.origin);result.set("image",value.selected);
  if(value.comparison&&value.comparison!==value.selected)result.set("compare",value.comparison);
  if(value.allVersions)result.set("versions","all");
 }
 return result;
}
export const revisionKey=(owner:string,design:string,revision:string)=>["v2",owner,"revision",design,revision] as const;
export function viewerReturn(value:unknown,owner:string|undefined,design:string|undefined):{key:string}|null{
 if(!value||typeof value!=="object")return null;
 const state=value as Record<string,unknown>;
 return state.owner===owner&&state.design===design&&typeof state.key==="string"?{key:state.key}:null;
}
