import {act,cleanup,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import i18n from "../../i18n";
import {runtimeUsage,runtimeUsageTotals,type RuntimeUsage} from "./api";
import {UsageCoverage,UsageDetails} from "./RuntimeUsage";
import zh from "./copy-zh-CN";
import en from "./copy-en";
import ja from "./copy-ja";
afterEach(cleanup);
const breakdown={inputTokens:12,cachedInputTokens:0,cacheWriteInputTokens:null,outputTokens:3,reasoningOutputTokens:1,totalTokens:15};
const attempt={phase:"generation",requested_model:"<b>{{model}}</b>",codex_version:"pinned",state:"completed",turn_acknowledged:true,
 token_status:"observed",token_scope:"upstream_thread_snapshot",token_snapshot:{last:breakdown,total:{...breakdown,inputTokens:90,totalTokens:93},modelContextWindow:null},
 observed_image_items:0,image_tool_calls:null,image_coverage:"through_terminal",created_at:"2026-09-26T00:00:00Z",updated_at:"2026-09-26T00:00:00Z"};
const raw={status:"observed",turn_tokens:null,provider_model_requests:null,amount:null,attempts:[attempt]};
const counts={dispatch_intents:2,acknowledged_attempts:1,reported_snapshots:1,missing_snapshots:1,invalid_snapshots:0,operations_without_observations:4,turn_tokens:null,provider_model_requests:null,amount:null};

it("rejects invalid counters and contradictory snapshot status instead of rendering zero",()=>{
 expect(runtimeUsage.safeParse(raw).success).toBe(true);
 for(const value of [-1,1.5,Number.MAX_SAFE_INTEGER+1,"12",true]){
  expect(runtimeUsageTotals.safeParse({...counts,dispatch_intents:value}).success).toBe(false);
 }
 expect(runtimeUsage.safeParse({...raw,attempts:[{...attempt,token_status:"unknown"}]}).success).toBe(false);
 expect(runtimeUsage.safeParse({...raw,attempts:[{...attempt,token_snapshot:null}]}).success).toBe(false);
 expect(runtimeUsage.safeParse({...raw,amount:0}).success).toBe(false);
});
it("strips fields outside the administrative usage contract",()=>{
 const value=runtimeUsage.parse({...raw,secret:"private",attempts:[{...attempt,thread_id:"private",token_snapshot:{
  ...attempt.token_snapshot,last:{...breakdown,prompt:"private"},
 }}]});
 expect(JSON.stringify(value)).not.toContain("private");
});
it("unavailable observations are never replaced by zero counters",async()=>{
 await act(async()=>{await i18n.changeLanguage("en");});
 render(<><UsageCoverage/><UsageDetails/></>);
 expect(screen.getAllByText(en.usageUnavailable)).toHaveLength(2);
 expect(screen.queryByText(en.usageDispatches)).toBeNull();
 expect(screen.queryByText("0")).toBeNull();
});
it("keeps explicit zero, unknown optional values, raw model names and expanded state across languages",async()=>{
 await act(async()=>{await i18n.changeLanguage("zh-CN");});
 const value=runtimeUsage.parse(raw);
 const {container}=render(<><UsageCoverage value={runtimeUsageTotals.parse(counts)}/><UsageDetails value={value}/></>);
 const disclosure=container.querySelector("details")!;disclosure.open=true;
 for(const [locale,c] of [["en",en],["ja",ja],["zh-CN",zh]] as const){
  await act(async()=>{await i18n.changeLanguage(locale);});
  const coverage=screen.getByRole("region",{name:c.usageCoverage});
  expect(within(coverage).getByText(c.usageUntracked).nextElementSibling).toHaveTextContent("4");
  expect(within(coverage).getByText(c.usageInvalid).nextElementSibling).toHaveTextContent("0");
  const region=screen.getByRole("region",{name:c.usageTitle});
  expect(region).toHaveTextContent("<b>{{model}}</b>");
  expect(within(region).getByRole("heading",{level:4})).toHaveTextContent(c.usageGeneration+" \u00b7 <b>{{model}}</b>");
  expect(container.querySelector("b")).toBeNull();
  expect(within(region).getAllByText(c.usageCached)[0].nextElementSibling).toHaveTextContent("0");
  expect(within(region).getAllByText(c.usageCacheWrite)[0].nextElementSibling).toHaveTextContent(c.unknown);
  expect(within(region).getAllByText(c.usageTokens).map(n=>n.nextElementSibling?.textContent)).toEqual(["15","93"]);
  expect(region).toHaveTextContent(c.usageNote);
  expect(container.querySelector("details")).toBe(disclosure);expect(disclosure.open).toBe(true);
 }
});
for(const status of ["unknown","invalid"] as const)it(status+" tokens keep a partial execution record visible",async()=>{
 await act(async()=>{await i18n.changeLanguage("en");});
 const value:RuntimeUsage=runtimeUsage.parse({...raw,attempts:[{...attempt,state:"started",token_status:status,token_snapshot:null,image_coverage:"partial"}]});
 const {container}=render(<UsageDetails value={value}/>);
 expect(screen.getByText(en.usageStarted)).toBeVisible();
 expect(screen.getByText(status==="invalid"?en.usageBadTokens:en.usageNoTokens)).toBeVisible();
 expect(screen.getByRole("region",{name:en.usageTitle})).toHaveTextContent(en.usagePartial);
 expect(container.querySelector("details")).toBeNull();
});


it("shows bounded image anomaly counts without exposing native output identities",async()=>{
 await act(()=>i18n.changeLanguage("en"));
 const value=runtimeUsage.parse({...raw,attempts:[{...attempt,image_anomalies:{
  replayed_items:2,conflicting_items:1,extra_completed_items:1,
 }}]});
 render(<UsageDetails value={value}/>);
 expect(screen.getByText(/Replayed items 2/)).toHaveTextContent("Identity conflicts 1");
 expect(screen.getByText(/Replayed items 2/)).toHaveTextContent("Extra completed image items 1");
});
