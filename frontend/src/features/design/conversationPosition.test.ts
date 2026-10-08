import {afterEach,expect,it,vi} from "vitest";
import {contentToken,readPosition,readingKey,savePosition,unreadSignal} from "./conversationPosition";
afterEach(()=>vi.restoreAllMocks());
it("keeps positions separate by account, branch, focus and browser entry",()=>{
 const key=readingKey("owner-a","design","main",null);
 const value={anchor:"message:one",offset:33,top:900,bottom:false,unread:true,latest:contentToken("private user text")};
 savePosition(key,value);
 expect(readPosition(key)).toEqual(value);
 expect(readPosition(readingKey("owner-b","design","main",null))).toBeNull();
 expect(readPosition(readingKey("owner-a","design","child",null))).toBeNull();
 expect(readPosition(readingKey("owner-a","design","main","focused-message"))).toBeNull();
 savePosition(JSON.stringify([key,"entry-a"]),value);
 expect(readPosition(JSON.stringify([key,"entry-b"]))).toBeNull();
 expect(JSON.stringify(sessionStorage)).not.toContain("private user text");
});
it("retains in-tab navigation when storage is unavailable",()=>{
 vi.spyOn(Storage.prototype,"setItem").mockImplementation(()=>{throw new Error("disabled");});
 const key=readingKey("memory-only","design","main",null),value={anchor:null,offset:0,top:0,bottom:true,unread:false};
 expect(()=>savePosition(key,value)).not.toThrow();expect(readPosition(key)).toEqual(value);
});
it("rejects malformed persisted positions",()=>{
 const key="malformed-reading-test";
 sessionStorage.setItem("kigcraft-reading-v1:"+key,JSON.stringify({anchor:7,offset:0,top:3,bottom:false,unread:false}));
 expect(readPosition(key)).toBeNull();
});
it("publishes one unread change for many streamed increments",()=>{
 const signal=unreadSignal(),listener=vi.fn(),unsubscribe=signal.subscribe(listener);
 for(let i=0;i<100;i++)signal.set(true);
 expect(listener).toHaveBeenCalledTimes(1);expect(signal.get()).toBe(true);
 signal.set(false);expect(listener).toHaveBeenCalledTimes(2);
 unsubscribe();signal.set(true);expect(listener).toHaveBeenCalledTimes(2);
});
