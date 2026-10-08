import {afterEach,expect,it,vi} from "vitest";
import {getCurrentUser,loginWithPassword,loginWithCode,registerAccount,logoutAccount,resetPassword,changePassword} from "./client";
const user={id:"owner",email:"owner@example.com",role:"user"};
const response=()=>Response.json(user);
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>resolve=done);return {promise,resolve};}
afterEach(()=>vi.unstubAllGlobals());
it("logout waits for the login response before sending the new cookie",async()=>{
 const first=deferred<Response>();const fetch=vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(Response.json({ok:true}));
 vi.stubGlobal("fetch",fetch);
 const login=loginWithPassword({email:user.email,password:"synthetic"});
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
 const logout=logoutAccount();await Promise.resolve();await Promise.resolve();
 expect(fetch).toHaveBeenCalledTimes(1);
 first.resolve(response());await login;await logout;
 expect(fetch.mock.calls.map(call=>call[0])).toEqual(["/api/auth/login/password","/api/auth/logout"]);
});
const mutations=[
 ()=>loginWithCode({email:user.email,code:"123456"}),
 ()=>registerAccount({email:user.email,code:"123456",password:"synthetic",termsAccepted:true,termsVersion:"v1"}),
 ()=>resetPassword({email:user.email,code:"123456",password:"synthetic"}),
 ()=>changePassword({currentPassword:"synthetic",newPassword:"replacement"}),
];
for(const [index,mutate] of mutations.entries())it("session mutation queues behind login "+index,async()=>{
 const first=deferred<Response>();const fetch=vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(Response.json({...user,ok:true}));
 vi.stubGlobal("fetch",fetch);
 const login=loginWithPassword({email:user.email,password:"synthetic"});
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
 const next=mutate();await Promise.resolve();await Promise.resolve();
 expect(fetch).toHaveBeenCalledTimes(1);
 first.resolve(response());await login;await next;expect(fetch).toHaveBeenCalledTimes(2);
});
it("authentication reads wait for preceding mutations and preserve cancellation",async()=>{
 const first=deferred<Response>(),controller=new AbortController();
 const fetch=vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(response());vi.stubGlobal("fetch",fetch);
 const login=loginWithPassword({email:user.email,password:"synthetic"});
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
 const reading=getCurrentUser(controller.signal);const rejected=expect(reading).rejects.toMatchObject({name:"AbortError"});
 controller.abort();first.resolve(response());await login;await rejected;
 expect(fetch).toHaveBeenCalledTimes(1);
 expect(await getCurrentUser()).toMatchObject({id:"owner"});
});
it("a failed login releases the next logout without swallowing its outcome",async()=>{
 const first=deferred<Response>();const fetch=vi.fn().mockReturnValueOnce(first.promise).mockResolvedValue(Response.json({ok:true}));vi.stubGlobal("fetch",fetch);
 const login=loginWithPassword({email:user.email,password:"synthetic"});
 const rejected=expect(login).rejects.toMatchObject({status:401});
 await vi.waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
 const logout=logoutAccount();await Promise.resolve();expect(fetch).toHaveBeenCalledTimes(1);
 first.resolve(Response.json({detail:"invalid_email_or_password"},{status:401}));
 await rejected;expect(await logout).toEqual({ok:true});
});
