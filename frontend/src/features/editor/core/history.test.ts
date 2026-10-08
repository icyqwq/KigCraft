import { describe, expect, it } from "vitest";
import { EditorHistory } from "./history";
import { createEmptyRecipe, updateFaceControl, updateEyeControl, updateLiquifyBrush } from "../deformation/recipe";
import { createDefaultLandmarks } from "../deformation/landmarks";
const initial = () => ({ recipe: createEmptyRecipe(), maskStrokes: [] });

describe("unified editor history", () => {
  it("undoes parameters, annotations, liquify and mask in chronological order, then redoes them", () => {
    const history = new EditorHistory(initial());
    const states = [history.snapshot];
    const face = updateFaceControl(history.snapshot.recipe, "faceWidth", .2);
    history.apply({ ...history.snapshot, recipe: face }); states.push(history.snapshot);
    const annotated = history.snapshot;
    annotated.recipe.annotations.push({ id:"a",kind:"pin",x:.4,y:.4,width:.1,height:.1,endX:.5,endY:.5,note:"keep" });
    history.apply(annotated); states.push(history.snapshot);
    const warped = updateLiquifyBrush(history.snapshot.recipe, {mode:"warp",x:.4,y:.4,radius:72,strength:.2,deltaX:.1});
    history.apply({ ...history.snapshot, recipe: warped }); states.push(history.snapshot);
    history.apply({ ...history.snapshot, maskStrokes:[{id:"m",mode:"brush",radius:36,points:[{x:.5,y:.5}]}] });
    states.push(history.snapshot);
    for (let i=states.length-2;i>=0;i--) expect(history.undo()).toEqual(states[i]);
    expect(history.canUndo).toBe(false);
    for (let i=1;i<states.length;i++) expect(history.redo()).toEqual(states[i]);
    expect(history.canRedo).toBe(false);
  });

  it("groups one continuous gesture, distinguishes consecutive gestures, and cancels only the active one", () => {
    const history = new EditorHistory(initial());
    for (const width of [.1,.2,.3]) history.apply({ ...history.snapshot,
      recipe:updateFaceControl(history.snapshot.recipe,"faceWidth",width) },"slider");
    history.commit();
    history.apply({ ...history.snapshot, recipe:updateFaceControl(history.snapshot.recipe,"faceWidth",.4) },"slider");
    expect(history.cancel().recipe.face.faceWidth).toBe(.3);
    expect(history.undo().recipe.face.faceWidth).toBe(0);
    expect(history.redo().recipe.face.faceWidth).toBe(.3);
  });

  it("keeps redo when a new gesture is cancelled but clears it after a new committed edit", () => {
    const history = new EditorHistory(initial());
    history.apply({ ...history.snapshot, recipe:updateFaceControl(history.snapshot.recipe,"faceWidth",.3) });
    history.undo();
    history.apply({ ...history.snapshot, recipe:updateEyeControl(history.snapshot.recipe,"eyeSize",.2) },"slider");
    history.cancel();
    expect(history.canRedo).toBe(true);
    history.apply({ ...history.snapshot, recipe:updateEyeControl(history.snapshot.recipe,"eyeSize",.4) });
    expect(history.canRedo).toBe(false);
  });

  it("rebases automatic landmarks across history without undoing them with earlier image edits", () => {
    const history = new EditorHistory(initial());
    history.apply({ ...history.snapshot, recipe:updateFaceControl(history.snapshot.recipe,"faceWidth",.3) });
    const landmarks = createDefaultLandmarks(1,1);
    history.rebaseRecipe({ ...history.snapshot.recipe, landmarks });
    expect(history.undo().recipe.landmarks).toEqual(landmarks);
    expect(history.snapshot.recipe.face.faceWidth).toBe(0);
    const changed = { ...landmarks, chin:{ x:.45,y:.8 } };
    history.apply({ ...history.snapshot, recipe:{...history.snapshot.recipe,landmarks:changed} });
    expect(history.undo().recipe.landmarks).toEqual(landmarks);
  });

  it("bounds history at 100 entries and isolates instances and caller snapshots", () => {
    const history = new EditorHistory(initial()), other = new EditorHistory(initial());
    for(let i=1;i<=110;i++) history.apply({ ...history.snapshot, maskStrokes:[{
      id:String(i),mode:"brush",radius:i,points:[{x:.5,y:.5}],
    }] });
    const snapshot = history.snapshot; snapshot.maskStrokes[0].radius=999;
    expect(history.snapshot.maskStrokes[0].radius).toBe(110);
    for(let i=0;i<100;i++) history.undo();
    expect(history.canUndo).toBe(false);
    expect(history.snapshot.maskStrokes[0].radius).toBe(10);
    expect(other.snapshot).toEqual(initial());
    expect(other.canUndo).toBe(false);
  });
});
