import type {RequestPreview as Preview} from "./api";
import {useReferenceCopy} from "../references/useReferenceCopy";
export function RequestPreview({value}:{value:Preview}){
 const c=useReferenceCopy();
 return <div className="v2-request-preview">
   {value.text?<p>{value.text}</p>:null}
   {value.base_image_url?<img src={value.base_image_url} alt={c("baseImage")}/>:null}
   {value.references.map((reference,index)=><figure key={index}>
     <img src={reference.image_url} alt={c("number",{number:reference.number??index+1})}/>
     <figcaption>{c("number",{number:reference.number??index+1})}{reference.usage?" · "+reference.usage:""}</figcaption>
   </figure>)}
 </div>;
}
