import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {ThemeProvider} from "@mui/material/styles";
import {act,cleanup,fireEvent,render,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {ApiError,currentUserSchema,getCurrentUser,loginWithPassword,getAuthCaptchaConfig,logoutAccount} from "../../api/client";
import {AuthProvider,useAuth,type AuthConfirmation} from "./AuthProvider";
import {isValidElement,StrictMode} from "react";
import {writeAuthDialogOpen} from "./authFormDraft";
import {useRequireAuth} from "./useRequireAuth";
import {kigTheme} from "../../app/theme";
import i18n from "../../i18n";
vi.mock("../../api/client",async original=>({...await original<typeof import("../../api/client")>(),
 getCurrentUser:vi.fn(),loginWithPassword:vi.fn(),getAuthCaptchaConfig:vi.fn(),logoutAccount:vi.fn()}));
const owner=currentUserSchema.parse({id:"owner",email:"owner@example.com",role:"user"});
function deferred<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>resolve=done);return {promise,resolve};}
const first=vi.fn(),second=vi.fn();const clients:QueryClient[]=[];
let currentAuth:ReturnType<typeof useAuth>;
let complete:(confirmation:AuthConfirmation)=>void;
function View(){
 const auth=useAuth(),gate=useRequireAuth();
 currentAuth=auth;
 if(isValidElement<{onAuthenticated:(confirmation:AuthConfirmation)=>void}>(gate.authDialog))complete=gate.authDialog.props.onAuthenticated;
 return <><output data-testid="identity">{auth.currentUser?.id??"guest"}</output>
 <button onClick={()=>gate.openAuthDialog(first)}>first action</button>
 <button onClick={()=>gate.openAuthDialog(second)}>second action</button>
 <button onClick={gate.closeAuthDialog}>close auth</button>
 <button onClick={auth.logout}>logout now</button>{gate.authDialog}</>;
}
function mount(strict=false){
 const client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});clients.push(client);
 const content=<ThemeProvider theme={kigTheme}><QueryClientProvider client={client}><AuthProvider><View/></AuthProvider></QueryClientProvider></ThemeProvider>;
 return render(strict?<StrictMode>{content}</StrictMode>:content);
}
async function submit(){
 fireEvent.click(screen.getByText("first action"));
 fireEvent.change(screen.getByLabelText(/^邮箱/),{target:{value:owner.email}});
 fireEvent.change(screen.getByLabelText(/^密码/),{target:{value:"Synthetic-password-2026"}});
 await waitFor(()=>expect(screen.getByRole("button",{name:"登录",exact:true})).toBeEnabled());
 fireEvent.click(screen.getByRole("button",{name:"登录",exact:true}));
 await waitFor(()=>expect(loginWithPassword).toHaveBeenCalledTimes(1));
}
beforeEach(async()=>{
 await i18n.changeLanguage("zh-CN");sessionStorage.clear();vi.clearAllMocks();
 vi.mocked(getCurrentUser).mockReset().mockRejectedValue(new ApiError("guest",401,{}));
 vi.mocked(loginWithPassword).mockReset();
 vi.mocked(getAuthCaptchaConfig).mockResolvedValue({enabled:false,site_key:null});
 vi.mocked(logoutAccount).mockResolvedValue({ok:true});
});
afterEach(()=>{cleanup();clients.splice(0).forEach(client=>client.clear());sessionStorage.clear();});
it("continues the original action exactly once after current identity is confirmed",async()=>{
 const login=deferred<typeof owner>();vi.mocked(loginWithPassword).mockReturnValue(login.promise);
 mount();await submit();vi.mocked(getCurrentUser).mockResolvedValue(owner);
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(first).toHaveBeenCalledTimes(1));expect(second).not.toHaveBeenCalled();
 expect(screen.queryByRole("button",{name:"登录",exact:true})).not.toBeInTheDocument();
});
it("does not continue a login that finishes after logout",async()=>{
 const login=deferred<typeof owner>();vi.mocked(loginWithPassword).mockReturnValue(login.promise);
 mount();await submit();fireEvent.click(screen.getByText("logout now"));
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(clients[0].getMutationCache().getAll().every(item=>item.state.status!=="pending")).toBe(true));
 expect(first).not.toHaveBeenCalled();expect(screen.getByTestId("identity")).toHaveTextContent("guest");
});
it("does not continue under a different account than the login response",async()=>{
 const login=deferred<typeof owner>();vi.mocked(loginWithPassword).mockReturnValue(login.promise);
 mount();await submit();vi.mocked(getCurrentUser).mockResolvedValue({...owner,id:"other",email:"other@example.com"});
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(clients[0].getMutationCache().getAll()[0].state.status).toBe("success"));
 expect(first).not.toHaveBeenCalled();
});
it("closing and reopening cannot let an old login consume the newer action or form",async()=>{
 const login=deferred<typeof owner>();vi.mocked(loginWithPassword).mockReturnValue(login.promise);
 mount();await submit();fireEvent.click(screen.getByText("close auth"));fireEvent.click(screen.getByText("second action"));
 fireEvent.change(screen.getByLabelText(/^邮箱/),{target:{value:"new-form@example.com"}});
 vi.mocked(getCurrentUser).mockResolvedValue(owner);
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(clients[0].getMutationCache().getAll()[0].state.status).toBe("success"));
 expect(first).not.toHaveBeenCalled();expect(second).not.toHaveBeenCalled();
 expect(screen.getByLabelText(/^邮箱/)).toHaveValue("new-form@example.com");
});
it("closing during confirmation prevents the pending action",async()=>{
 const login=deferred<typeof owner>(),confirmation=deferred<typeof owner>();
 vi.mocked(loginWithPassword).mockReturnValue(login.promise);mount();await submit();
 vi.mocked(getCurrentUser).mockReturnValue(confirmation.promise);
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(getCurrentUser).toHaveBeenCalledTimes(2));
 fireEvent.click(screen.getByText("close auth"));
 await act(async()=>{confirmation.resolve(owner);await confirmation.promise;});
 await waitFor(()=>expect(clients[0].getMutationCache().getAll()[0].state.status).toBe("success"));
 expect(first).not.toHaveBeenCalled();
});
it("unmounting the page cancels its deferred business continuation",async()=>{
 const login=deferred<typeof owner>();vi.mocked(loginWithPassword).mockReturnValue(login.promise);
 const view=mount();await submit();view.unmount();vi.mocked(getCurrentUser).mockResolvedValue(owner);
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(clients[0].getMutationCache().getAll()[0].state.status).toBe("success"));
 expect(first).not.toHaveBeenCalled();
});

it("duplicate confirmed callbacks cannot consume an action twice",async()=>{
 mount();await waitFor(()=>expect(clients[0].getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
 fireEvent.click(screen.getByText("first action"));vi.mocked(getCurrentUser).mockResolvedValue(owner);
 let confirmation:AuthConfirmation|null=null;
 await act(async()=>{confirmation=await currentAuth.refreshAuth();});
 expect(confirmation).not.toBeNull();
 const callback=complete;
 await act(async()=>{callback(confirmation!);callback(confirmation!);});
 expect(first).toHaveBeenCalledTimes(1);
});
it("a confirmed callback becomes unusable immediately on logout",async()=>{
 mount();await waitFor(()=>expect(clients[0].getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
 fireEvent.click(screen.getByText("first action"));vi.mocked(getCurrentUser).mockResolvedValue(owner);
 let confirmation:AuthConfirmation|null=null;
 await act(async()=>{confirmation=await currentAuth.refreshAuth();});
 expect(confirmation).not.toBeNull();
 const callback=complete;vi.mocked(getCurrentUser).mockRejectedValue(new ApiError("guest",401,{}));
 fireEvent.click(screen.getByText("logout now"));
 await act(async()=>{callback(confirmation!);});
 expect(first).not.toHaveBeenCalled();
});
it("offline cached identity is retained but does not grant a fresh confirmation",async()=>{
 vi.mocked(getCurrentUser).mockResolvedValue(owner);mount();
 await waitFor(()=>expect(screen.getByTestId("identity")).toHaveTextContent("owner"));
 await waitFor(()=>expect(clients[0].getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
 vi.mocked(getCurrentUser).mockRejectedValue(new TypeError("offline"));
 let confirmation:AuthConfirmation|null|undefined;
 await act(async()=>{confirmation=await currentAuth.refreshAuth();});
 expect(confirmation).toBeNull();expect(screen.getByTestId("identity")).toHaveTextContent("owner");
});
it("a stale login epoch is refused without another authentication read",async()=>{
 mount();await waitFor(()=>expect(clients[0].getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
 const epoch=currentAuth.authSessionEpoch;
 fireEvent.click(screen.getByText("logout now"));
 await waitFor(()=>expect(clients[0].getMutationCache().getAll()[0].state.status).toBe("success"));
 const count=vi.mocked(getCurrentUser).mock.calls.length;
 expect(await currentAuth.refreshAuth(epoch)).toBeNull();
 expect(getCurrentUser).toHaveBeenCalledTimes(count);
});

it("an old confirmation cannot close a reopened dialog or consume its new action",async()=>{
 const login=deferred<typeof owner>(),confirmation=deferred<typeof owner>();
 vi.mocked(loginWithPassword).mockReturnValue(login.promise);mount();await submit();
 vi.mocked(getCurrentUser).mockReturnValue(confirmation.promise);
 await act(async()=>{login.resolve(owner);await login.promise;});
 await waitFor(()=>expect(getCurrentUser).toHaveBeenCalledTimes(2));
 fireEvent.click(screen.getByText("close auth"));fireEvent.click(screen.getByText("second action"));
 fireEvent.change(screen.getByLabelText(/^邮箱/),{target:{value:"new-form@example.com"}});
 await act(async()=>{confirmation.resolve(owner);await confirmation.promise;});
 await waitFor(()=>expect(clients[0].getMutationCache().getAll()[0].state.status).toBe("success"));
 expect(first).not.toHaveBeenCalled();expect(second).not.toHaveBeenCalled();
 expect(screen.getByLabelText(/^邮箱/)).toHaveValue("new-form@example.com");
});

it("a restored dialog still closes for an existing login under StrictMode without continuing anything",async()=>{
 writeAuthDialogOpen(true);vi.mocked(getCurrentUser).mockResolvedValue(owner);mount(true);
 await waitFor(()=>expect(screen.getByTestId("identity")).toHaveTextContent("owner"));
 await waitFor(()=>expect(screen.queryByRole("button",{name:"登录",exact:true})).not.toBeInTheDocument());
 expect(first).not.toHaveBeenCalled();expect(second).not.toHaveBeenCalled();
});
