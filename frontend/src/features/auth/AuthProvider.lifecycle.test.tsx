import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError, getCurrentUser, logoutAccount, type CurrentUser } from "../../api/client";
import { AuthProvider, useAuth } from "./AuthProvider";

vi.mock("../../api/client", async original => ({
  ...await original<typeof import("../../api/client")>(),
  getCurrentUser: vi.fn(),
  logoutAccount: vi.fn(),
}));
const user: CurrentUser = {
  id:"owner",email:"owner@example.com",role:"user",is_banned:false,
  daily_generation_remaining:3,daily_generation_reset_at:null,
  terms_version:"v2",terms_accepted_at:null,generation_quality_improvement_consent:false,
  generation_quality_improvement_consent_updated_at:null,
};
const clients: QueryClient[] = [];
function deferred<T>() {
  let resolve!: (value:T)=>void;
  const promise = new Promise<T>(done=>{resolve=done;});
  return {promise,resolve};
}
function View() {
  const auth = useAuth();
  return <><output data-testid="identity">{auth.currentUser?.id??"guest"}</output>
    <output data-testid="epoch">{auth.authSessionEpoch}</output>
    <button onClick={auth.logout}>logout</button>
    <button onClick={()=>void auth.refreshAuth()}>refresh</button></>;
}
function mount() {
  const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
  clients.push(client);client.setQueryData(["auth","me"],user);
  render(<QueryClientProvider client={client}><AuthProvider><View/></AuthProvider></QueryClientProvider>);
  return client;
}
beforeEach(()=>{
  vi.mocked(getCurrentUser).mockReset();
  vi.mocked(logoutAccount).mockReset();
});
afterEach(()=>{cleanup();for(const client of clients.splice(0))client.clear();});

it.each([401,403])("clears cached identity after an authoritative %i response",async status=>{
  vi.mocked(getCurrentUser).mockRejectedValue(new ApiError("unavailable",status,{}));
  const client=mount();
  await waitFor(()=>expect(client.getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
  expect(screen.getByTestId("identity")).toHaveTextContent("guest");
  expect(client.getQueryData(["auth","me"])).toBeNull();
});

it("keeps the cached identity on a temporary network failure",async()=>{
  vi.mocked(getCurrentUser).mockRejectedValue(new TypeError("offline"));
  const client=mount();
  await waitFor(()=>expect(client.getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
  expect(screen.getByTestId("identity")).toHaveTextContent("owner");
});

it("a pre-logout authentication response cannot restore the user",async()=>{
  const pending=deferred<CurrentUser>(), logout=deferred<{ok:boolean}>();
  vi.mocked(getCurrentUser).mockReturnValue(pending.promise);
  vi.mocked(logoutAccount).mockReturnValue(logout.promise);
  const client=mount();
  await waitFor(()=>expect(getCurrentUser).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByText("logout"));
  expect(screen.getByTestId("identity")).toHaveTextContent("guest");
  await act(async()=>{pending.resolve(user);await pending.promise;});
  await waitFor(()=>expect(client.getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
  expect(screen.getByTestId("identity")).toHaveTextContent("guest");
  expect(client.getQueryData(["auth","me"])).toBeNull();
});

it("a late logout callback cannot clear a newer confirmed login",async()=>{
  const logout=deferred<{ok:boolean}>();
  vi.mocked(getCurrentUser).mockResolvedValue(user);
  vi.mocked(logoutAccount).mockReturnValue(logout.promise);
  const client=mount();
  await waitFor(()=>expect(client.getQueryState(["auth","me"])?.fetchStatus).toBe("idle"));
  fireEvent.click(screen.getByText("logout"));
  vi.mocked(getCurrentUser).mockResolvedValue({...user,id:"new-owner"});
  fireEvent.click(screen.getByText("refresh"));
  await waitFor(()=>expect(screen.getByTestId("identity")).toHaveTextContent("new-owner"));
  vi.mocked(getCurrentUser).mockReturnValue(deferred<CurrentUser>().promise);
  await act(async()=>{logout.resolve({ok:true});await logout.promise;});
  await waitFor(()=>expect(client.getMutationCache().getAll()[0].state.status).toBe("success"));
  expect(screen.getByTestId("identity")).toHaveTextContent("new-owner");
});

it("passes the query abort signal through to the authentication request",async()=>{
  vi.mocked(getCurrentUser).mockResolvedValue(user);
  mount();
  await waitFor(()=>expect(getCurrentUser).toHaveBeenCalledWith(expect.any(AbortSignal)));
});
