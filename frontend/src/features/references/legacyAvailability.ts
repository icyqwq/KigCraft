import {useEffect,useState} from "react";

/** True when the account has imported legacy reference images. A failed or empty read hides the menu entry. */
export function useHasLegacyReferences(ownerId:string|undefined){
 const [available,setAvailable]=useState(false);
 useEffect(()=>{
  setAvailable(false);
  if(!ownerId||typeof fetch!=="function")return;
  const controller=new AbortController();
  void fetch("/api/v2/account/legacy-materials?kind=reference",{credentials:"include",signal:controller.signal})
   .then(response=>response.ok?response.json():null)
   .then(body=>{if(!controller.signal.aborted)setAvailable(Array.isArray(body?.items)&&body.items.length>0);})
   .catch(()=>{});
  return()=>controller.abort();
 },[ownerId]);
 return available;
}
