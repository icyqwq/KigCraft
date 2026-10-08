import {readFileSync} from "node:fs";
import {describe,expect,it} from "vitest";
import {editorDocumentSchema} from "./documentSchema";
const cases=JSON.parse(readFileSync("../docs/design/fixtures/v2-editor-document.json","utf8")) as {
 valid:{name:string;document:any}[];invalid:{name:string;path:(string|number)[];value?:unknown;remove?:boolean}[];
};
describe("shared versioned editor document contract",()=>{
 it.each(cases.valid)("$name retains every wire value",({document})=>{
   const original=structuredClone(document);
   expect(editorDocumentSchema.parse(document)).toEqual(original);
   expect(document).toEqual(original);
 });
 it.each(cases.invalid)("$name is rejected",(test)=>{
   const value=structuredClone(cases.valid.at(-1)!.document);
   const target=test.path.slice(0,-1).reduce((target,key)=>target[key],value),key=test.path.at(-1)!;
   if(test.remove)delete target[key];else target[key]=test.value;
   expect(editorDocumentSchema.safeParse(value).success).toBe(false);
 });
});
