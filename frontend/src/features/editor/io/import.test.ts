import { describe, expect, it } from "vitest";
import { createEmptyRecipe, updateFaceControl } from "../deformation/recipe";
import { embedEditRecipeInPng } from "../imageRecipeMetadata";
import { prepareFlatImageImport } from "./import";
const png = new Uint8Array([137,80,78,71,13,10,26,10,0,0,0,0,73,69,78,68,174,66,96,130]);

function fixture(metadata: unknown, corrupt = false) {
  const keyword = new TextEncoder().encode("kigcraft.edit-recipe");
  const json = new TextEncoder().encode(JSON.stringify(metadata));
  const data = new Uint8Array(keyword.length + 5 + json.length); data.set(keyword); data.set(json, keyword.length + 5);
  const chunk = new Uint8Array(data.length + 12), view = new DataView(chunk.buffer);
  view.setUint32(0, data.length); chunk.set(new TextEncoder().encode("iTXt"),4);chunk.set(data,8);
  let crc = 0xffffffff;
  for(const byte of chunk.subarray(4,8+data.length)) {
    crc ^= byte; for(let bit=0;bit<8;bit++) crc=(crc>>>1)^(crc&1?0xedb88320:0);
  }
  view.setUint32(8+data.length,((crc^0xffffffff)>>>0) ^ (corrupt ? 1 : 0));
  return new Blob([png.slice(0,8),chunk,png.slice(8)],{type:"image/png"});
}

describe("flat PNG import", () => {
  it("keeps legacy geometry as provenance without applying it to already rendered pixels", async () => {
    const recipe=updateFaceControl(createEmptyRecipe(),"faceWidth",-.2);
    recipe.liquify=[{mode:"warp",radius:72,x:.4,y:.4,strength:.3,deltaX:.1}];
    recipe.annotations=[{id:"old",kind:"pin",x:.2,y:.2,width:.1,height:.1,endX:.3,endY:.3,note:"old note"}];
    const imported=await prepareFlatImageImport(await embedEditRecipeInPng(new Blob([png]),recipe));
    expect(imported.recipe).toEqual(createEmptyRecipe());
    expect(imported.sourceRecipe).toEqual(recipe);
    expect(imported.recipe.landmarks).toBeUndefined();
    expect(imported.recipe.imageCoordinates).toBeUndefined();
  });

  it.each(["bad CRC","future version","oversized metadata","malformed recipe"])("ignores %s without losing the flat image import", async kind => {
    const record={schema:"kigcraft.edit-recipe",version:kind==="future version"?99:1,recipe:createEmptyRecipe()};
    if(kind==="oversized metadata")record.recipe.details.accessoryNote="x".repeat(4*1024*1024);
    if(kind==="malformed recipe")Object.assign(record.recipe,{face:{faceWidth:"invalid"}});
    const imported=await prepareFlatImageImport(fixture(record,kind==="bad CRC"));
    expect(imported.recipe).toEqual(createEmptyRecipe());
    expect(imported.sourceRecipe).toBeNull();
  });

  it("opens ordinary files without inventing an edit record", async () => {
    expect(await prepareFlatImageImport(new Blob(["jpeg"],{type:"image/jpeg"}))).toEqual({
      recipe:createEmptyRecipe(),sourceRecipe:null,
    });
  });
});
