/** Match the server projection of the frozen recipe, never the live canvas. */
export function numberedAnnotations(value:unknown){
 if(!Array.isArray(value))return [];
 return value.flatMap((mark,index)=>{
  if(!mark||typeof mark!=="object"||typeof mark.id!=="string"||!mark.id)return [];
  return [{annotation_id:mark.id,number:index+1,note:typeof mark.note==="string"?mark.note:"",
    text:typeof mark.text==="string"?mark.text:""}];
 });
}
