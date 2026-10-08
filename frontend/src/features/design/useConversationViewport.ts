import {useCallback,useLayoutEffect,useMemo,useRef,useState,type RefObject} from "react";
import {capturePosition,contentToken,readPosition,restorePosition,savePosition,unreadSignal,type ReadingPosition} from "./conversationPosition";
import type {historyTimeline} from "./historyTimeline";

type Timeline=ReturnType<typeof historyTimeline>;
type Options={container:RefObject<HTMLElement|null>;scope:string|null;entry:string;ready:boolean;focus?:string;
 forceBottom:boolean;items:Timeline;latest:Timeline;fetching:boolean;fetchingMore:boolean;hasMore:boolean;failed:boolean;
 fetchMore:()=>unknown};
export function useConversationViewport(options:Options){
 const live=useRef(options);live.current=options;
 const [settled,setSettled]=useState(0);
 const signal=useMemo(unreadSignal,[options.scope,options.entry]);
 const state=useRef<{identity:string;initialized:boolean;pending:ReadingPosition|null;follow:boolean;
  position:ReadingPosition|null;older:ReadingPosition|null;latest:Map<string,string>;tail:string|null;requestPending:boolean;latestToken:string}|null>(null);
 const identity=JSON.stringify([options.scope,options.entry]);
 if(state.current?.identity!==identity){
  state.current={identity,initialized:false,pending:null,follow:true,position:null,older:null,latest:new Map(),tail:null,requestPending:false,latestToken:""};
 }
 const persist=useCallback(()=>{
  const {container,scope,entry}=live.current,s=state.current;
  if(!container.current||!scope||!s?.initialized||s.pending)return;
  s.position={...capturePosition(container.current,s.follow,signal.get()),latest:s.latestToken};
  savePosition(scope,s.position);savePosition(JSON.stringify([scope,entry]),s.position);
 },[signal]);
 const onScroll=useCallback(()=>{
  const el=live.current.container.current,s=state.current;
  if(!el||!s?.initialized||s.pending)return;
  s.follow=!live.current.focus&&el.scrollHeight-el.scrollTop-el.clientHeight<100;
  if(s.follow)signal.set(false);
  else if(live.current.fetchingMore)s.older=capturePosition(el,false,signal.get());
  persist();
 },[persist,signal]);
 const onUserScroll=useCallback(()=>{
  const s=state.current;
  if(s){s.pending=null;s.older=null;s.follow=false;persist();}
 },[persist]);
 const toBottom=useCallback(()=>{
  const s=state.current,el=live.current.container.current;
  if(!s||!el)return;
  s.pending=null;s.older=null;s.follow=true;signal.set(false);
  // The explicit jump glides; following a stream stays immediate (see onStream).
  const reduced=typeof window.matchMedia==="function"&&window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if(typeof el.scrollTo==="function")el.scrollTo({top:el.scrollHeight,behavior:reduced?"auto":"smooth"});else el.scrollTop=el.scrollHeight;
  persist();
 },[persist,signal]);
 const onStream=useCallback(()=>{
  const s=state.current,el=live.current.container.current;
  if(!s?.initialized||!el)return;
  if(s.follow&&!s.pending)el.scrollTop=el.scrollHeight;
  else signal.set(true);
  persist();
 },[persist,signal]);
 const loadOlder=useCallback(()=>{
  const {container,fetching,fetchMore}=live.current,s=state.current;
  if(!container.current||!s||fetching)return;
  s.older=s.pending?null:capturePosition(container.current,false,signal.get());s.follow=false;
  void fetchMore();
 },[signal]);

 useLayoutEffect(()=>{
  const {container,scope,entry,ready,latest,focus,forceBottom,fetching,fetchingMore,hasMore,failed,fetchMore}=live.current;
  const el=container.current,s=state.current;
  if(!scope||!ready||!el||!s)return;
  const signature=(item:Timeline[number])=>item.type==="message"?JSON.stringify([item.message.status,item.message.content_parts]):item.id;
  const next=new Map(latest.map(item=>[item.type+":"+item.id,signature(item)]));
  const token=contentToken(Array.from(next));
  const tail=latest.length?latest.at(-1)!.type+":"+latest.at(-1)!.id:null;
  if(!s.initialized){
   s.initialized=true;
   const stored=readPosition(JSON.stringify([scope,entry]))??(forceBottom?null:readPosition(scope));
   s.pending=stored;s.follow=!focus&&(stored?.bottom??true);signal.set(Boolean(stored?.unread||stored&&!stored.bottom&&stored.latest&&stored.latest!==token));
   if(!stored&&focus){
    const node=Array.from(el.querySelectorAll<HTMLElement>("[data-history-id]")).find(node=>node.dataset.historyId==="message:"+focus);
    if(node){el.scrollTop+=node.getBoundingClientRect().top-el.getBoundingClientRect().top-el.clientHeight/2+node.clientHeight/2;}
   }else if(!stored){el.scrollTop=el.scrollHeight;}
  }else{
   const priorTailIndex=latest.findIndex(item=>item.type+":"+item.id===s.tail);
   const appended=Boolean(s.tail&&tail!==s.tail&&(priorTailIndex>=0?priorTailIndex<latest.length-1:latest.length>0));
   const changed=Array.from(next).some(([key,value])=>s.latest.has(key)&&s.latest.get(key)!==value);
   if((appended||changed)&&!s.follow)signal.set(true);
  }
  s.latest=next;s.tail=tail;s.latestToken=token;
  if(s.pending){
   if(restorePosition(el,s.pending)){s.pending=null;s.older=null;s.requestPending=false;}
   else if(hasMore){
    if(!fetching&&!failed&&!s.requestPending){s.requestPending=true;void Promise.resolve(fetchMore()).finally(()=>{s.requestPending=false;if(state.current===s)setSettled(value=>value+1);});}
    return;
   }else if(!fetching){el.scrollTop=s.pending.top;s.pending=null;}
   else return;
  }else if(s.older&&!fetchingMore){
   restorePosition(el,s.older);s.older=null;
  }else if(s.follow&&!fetchingMore){el.scrollTop=el.scrollHeight;}
  persist();
 },[identity,settled,options.ready,options.items,options.latest,options.fetching,options.fetchingMore,options.hasMore,options.failed,persist,signal]);

 useLayoutEffect(()=>{
  const el=options.container.current;if(!el)return;
  const observer=new ResizeObserver(()=>{
   const s=state.current;if(!s?.initialized||s.pending)return;
   if(s.follow)el.scrollTop=el.scrollHeight;
   else if(s.position)restorePosition(el,s.position);
   persist();
  });
  observer.observe(el);
  return()=>observer.disconnect();
 },[identity,options.container,persist]);
 return {signal,onScroll,onUserScroll,toBottom,onStream,loadOlder};
}
