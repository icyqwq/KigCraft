import {describe,it,expect} from "vitest";
import en from "./copy-en";
import ja from "./copy-ja";
import zh from "./copy-zh-CN";
describe("editor copy interpolation",()=>{
  for(const [locale,copy] of Object.entries({en,ja}))it(locale+" preserves every resource and interpolation argument",()=>{
    expect(Object.keys(copy).sort()).toEqual(Object.keys(zh).sort());
    for(const key of Object.keys(zh) as (keyof typeof zh)[]){
      expect(copy[key].trim(),key).not.toBe("");
      const parameters=(value:string)=>[...value.matchAll(/\{\{(\w+)\}\}/g)].map(match=>match[1]).sort();
      expect(parameters(copy[key]),key).toEqual(parameters(zh[key]));
    }
  });
});
