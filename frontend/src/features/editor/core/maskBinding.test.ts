import { describe, expect, it } from "vitest";
import { bindMask, maskMatches } from "./maskBinding";
import { EditorHistory } from "./history";
import { createEmptyRecipe, normalizeEditRecipe, updateFaceControl } from "../deformation/recipe";

describe("mask geometry binding", () => {
  it("binds a mask to its source dimensions and geometry, excluding annotation wording", () => {
    const recipe = createEmptyRecipe(), binding = bindMask(recipe, "asset:a", 2048, 1024);
    expect(maskMatches(binding, recipe, "asset:a", 2048, 1024)).toBe(true);
    expect(maskMatches(binding, recipe, "asset:b", 2048, 1024)).toBe(false);
    expect(maskMatches(binding, recipe, "asset:a", 1024, 1024)).toBe(false);
    expect(maskMatches(binding, updateFaceControl(recipe,"faceWidth",.2), "asset:a", 2048, 1024)).toBe(false);
    expect(maskMatches(binding, { ...recipe, annotations:[{
      id:"a",kind:"pin",x:.5,y:.5,width:.1,height:.1,endX:.5,endY:.5,note:"change this",
    }] }, "asset:a", 2048, 1024)).toBe(true);
  });

  it("keeps a binding valid after recipe normalization reorders control keys", () => {
    const recipe=createEmptyRecipe(),binding=bindMask(recipe,"a",640,480);
    const normalized=normalizeEditRecipe(recipe);
    expect(JSON.stringify(recipe.eyes)).not.toBe(JSON.stringify(normalized.eyes));
    expect(maskMatches(binding,normalized,"a",640,480)).toBe(true);
    expect(maskMatches(binding,updateFaceControl(normalized,"faceWidth",.1),"a",640,480)).toBe(false);
  });

  it("rejects malformed, incomplete or changed geometry while retaining array order", () => {
    const recipe=createEmptyRecipe();
    recipe.liquify=[
      {x:.3,y:.5,radius:30,strength:.1,mode:"warp",deltaX:.02},
      {x:.6,y:.4,radius:20,strength:.2,mode:"expand"},
    ];
    const binding=bindMask(recipe,"a",640,480);
    for(const geometry of ["{", "null", "[]", "{}", JSON.stringify({...JSON.parse(binding.geometry),face:null})]){
      expect(maskMatches({...binding,geometry},recipe,"a",640,480)).toBe(false);
    }
    expect(maskMatches(binding,{...recipe,liquify:[...recipe.liquify].reverse()},"a",640,480)).toBe(false);
    expect(maskMatches(binding,{...recipe,imageCoordinates:{version:1,width:640,height:480,effectScale:2}},"a",640,480)).toBe(false);
  });

  it("restores selection validity when undoing geometry and makes explicit confirmation undoable", () => {
    const recipe = createEmptyRecipe(), binding = bindMask(recipe, "a", 1024, 1024);
    const history = new EditorHistory({ recipe,maskBinding:binding,maskStrokes:[
      {id:"m",mode:"brush",radius:36,points:[{x:.5,y:.5}]},
    ]});
    const valid = () => maskMatches(history.snapshot.maskBinding,history.snapshot.recipe,"a",1024,1024);
    history.apply({...history.snapshot,recipe:updateFaceControl(recipe,"faceWidth",.2)});
    expect(valid()).toBe(false);
    history.undo(); expect(valid()).toBe(true);
    history.redo(); expect(valid()).toBe(false);
    history.apply({...history.snapshot,maskBinding:bindMask(history.snapshot.recipe,"a",1024,1024)});
    expect(valid()).toBe(true);
    history.undo(); expect(valid()).toBe(false);
    history.redo(); expect(valid()).toBe(true);
  });
});
