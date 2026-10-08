export type ReadingPosition={anchor:string|null;offset:number;top:number;bottom:boolean;unread:boolean;latest?:string};
const memory=new Map<string,ReadingPosition>();
const prefix="kigcraft-reading-v1:";
export function readingKey(owner:string,design:string,branch:string,message:string|null){
 return JSON.stringify([owner,design,branch,message]);
}
export function readPosition(key:string):ReadingPosition|null{
 if(memory.has(key))return memory.get(key)!;
 try{
  const raw=sessionStorage.getItem(prefix+key);
  if(!raw)return null;
  const value=JSON.parse(raw);
  if((value.anchor!==null&&typeof value.anchor!=="string")||!Number.isFinite(value.offset)||!Number.isFinite(value.top)||
    value.top<0||typeof value.bottom!=="boolean"||typeof value.unread!=="boolean"||(value.latest!==undefined&&typeof value.latest!=="string"))return null;
  return value;
 }catch{return null;}
}
export function savePosition(key:string,value:ReadingPosition){
 memory.delete(key);memory.set(key,value);
 while(memory.size>200)memory.delete(memory.keys().next().value!);
 try{
  sessionStorage.setItem(prefix+key,JSON.stringify(value));
  const keys=Object.keys(sessionStorage).filter(key=>key.startsWith(prefix));
  for(const old of keys.slice(0,Math.max(0,keys.length-200)))sessionStorage.removeItem(old);
 }catch{/* Reading remains usable if tab storage is disabled. */}
}
export function capturePosition(container:HTMLElement,bottom:boolean,unread:boolean):ReadingPosition{
 if(bottom)return {anchor:null,offset:0,top:container.scrollTop,bottom,unread};
 const top=container.getBoundingClientRect().top;
 const anchor=Array.from(container.querySelectorAll<HTMLElement>("[data-history-id]"))
  .find(node=>node.getBoundingClientRect().bottom>top);
 return {anchor:anchor?.dataset.historyId??null,offset:anchor?anchor.getBoundingClientRect().top-top:0,
  top:container.scrollTop,bottom,unread};
}
export function restorePosition(container:HTMLElement,value:ReadingPosition){
 if(value.bottom){container.scrollTop=container.scrollHeight;return true;}
 const node=Array.from(container.querySelectorAll<HTMLElement>("[data-history-id]"))
  .find(node=>node.dataset.historyId===value.anchor);
 if(node){container.scrollTop+=node.getBoundingClientRect().top-container.getBoundingClientRect().top-value.offset;return true;}
 return false;
}
export function contentToken(values:unknown){
 const text=JSON.stringify(values);let hash=2166136261;
 for(let index=0;index<text.length;index++)hash=Math.imul(hash^text.charCodeAt(index),16777619);
 return (hash>>>0).toString(36);
}
export function unreadSignal(){
 let value=false;const listeners=new Set<()=>void>();
 return {get:()=>value,subscribe:(fn:()=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn);};},
  set:(next:boolean)=>{if(next!==value){value=next;listeners.forEach(fn=>fn());}}};
}
