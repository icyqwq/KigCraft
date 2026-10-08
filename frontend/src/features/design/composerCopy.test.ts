import {it,expect} from "vitest";
import en from "./copy-en";
import ja from "./copy-ja";
import zh from "./copy-zh-CN";
import errorsEn from "./errors-en";
import errorsJa from "./errors-ja";
import errorsZh from "./errors-zh-CN";
import refsEn from "../references/copy-en";
import refsJa from "../references/copy-ja";
import refsZh from "../references/copy-zh-CN";
import {DesignApiError,errorText} from "./api";
for(const [group,source,copies] of [["composer",zh,{en,ja}],["references",refsZh,{en:refsEn,ja:refsJa}],["errors",errorsZh,{en:errorsEn,ja:errorsJa}]] as const){
  for(const [locale,copy] of Object.entries(copies))it(group+" "+locale+" preserves keys and interpolation parameters",()=>{
    expect(Object.keys(copy).sort()).toEqual(Object.keys(source).sort());
    const parameters=(value:string)=>[...value.matchAll(/\{\{(\w+)\}\}/g)].map(match=>match[1]).sort();
    for(const [key,value] of Object.entries(source)){
      const translated=(copy as Record<string,string>)[key];
      expect(translated.trim(),key).not.toBe("");
      expect(parameters(translated),key).toEqual(parameters(value));
    }
  });
}
it("uses safe localized errors without exposing arbitrary server content or inherited properties",()=>{
  for(const [locale,copy] of [["zh-CN",errorsZh],["en-US",errorsEn],["ja-JP",errorsJa]] as const){
    expect(errorText(new DesignApiError("quota_insufficient",403),locale)).toBe(copy.quota_insufficient);
    expect(errorText(new DesignApiError("submission_rate_limited",429),locale)).toBe(copy.submission_rate_limited);
    expect(copy.submission_rate_limited).not.toBe(copy.quota_insufficient);
    expect(errorText(new DesignApiError("anything",401),locale)).toBe(copy.sign_in_required);
    for(const error of [new Error("private server detail"),"__proto__","constructor","<b>server</b>"])
      expect(errorText(error,locale)).toBe(copy.request_failed);
  }
});
