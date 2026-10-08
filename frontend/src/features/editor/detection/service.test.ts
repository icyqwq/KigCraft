import {afterEach,expect,it,vi} from "vitest";
import {detectEditorLandmarks} from "./service";
const detector=vi.hoisted(()=>({detect:vi.fn()}));
vi.mock("../deformation/animeLandmarkDetector",()=>({detectAnimeLandmarks:detector.detect}));
afterEach(()=>vi.resetAllMocks());

it("an already cancelled request never reaches the model",async()=>{
  const controller=new AbortController();controller.abort();
  await expect(detectEditorLandmarks(new Image(),controller.signal)).rejects.toMatchObject({name:"AbortError"});
  expect(detector.detect).not.toHaveBeenCalled();
});

it("one editor cancelling does not discard another editor's request",async()=>{
  let finish!:()=>void;
  const pending=new Promise<null>(resolve=>{finish=()=>resolve(null);});
  detector.detect.mockReturnValue(pending);
  const first=new AbortController(),second=new AbortController();
  const left=detectEditorLandmarks(new Image(),first.signal),right=detectEditorLandmarks(new Image(),second.signal);
  await vi.waitFor(()=>expect(detector.detect).toHaveBeenCalledTimes(2));
  first.abort();
  const rejected=expect(left).rejects.toMatchObject({name:"AbortError"});
  finish();
  await rejected;
  await expect(right).resolves.toBeNull();
  expect(second.signal.aborted).toBe(false);
});

it("model failure can be retried explicitly without retaining a rejected request",async()=>{
  detector.detect.mockRejectedValueOnce(new Error("model unavailable")).mockResolvedValueOnce(null);
  await expect(detectEditorLandmarks(new Image(),new AbortController().signal)).rejects.toThrow("model unavailable");
  await expect(detectEditorLandmarks(new Image(),new AbortController().signal)).resolves.toBeNull();
});
