import {afterEach,expect,it,vi} from "vitest";
const mock=vi.hoisted(()=>({detect:vi.fn(),run:vi.fn(),session:vi.fn(),face:vi.fn()}));
vi.mock("@mediapipe/tasks-vision",()=>({
 FilesetResolver:{forVisionTasks:vi.fn().mockResolvedValue({})},
 FaceDetector:{createFromOptions:mock.face},
}));
vi.mock("onnxruntime-web/wasm",()=>({
 env:{wasm:{}},InferenceSession:{create:mock.session},
 Tensor:class {constructor(..._args:unknown[]){}},
}));
afterEach(()=>{vi.resetModules();vi.resetAllMocks();vi.restoreAllMocks();});
async function prepare(detections:unknown[]){
 mock.detect.mockReturnValue({detections});
 mock.face.mockResolvedValue({detect:mock.detect});
 mock.session.mockResolvedValue({inputNames:["input"],outputNames:["output"],run:mock.run});
 const image=new Image();
 Object.defineProperty(image,"naturalWidth",{value:640});
 Object.defineProperty(image,"naturalHeight",{value:480});
 const implementation=await import("./animeLandmarkDetector");
 return {image,...implementation};
}
const face={boundingBox:{originX:180,originY:80,width:240,height:300},categories:[{score:.9}]};
it("finding no face never reaches landmark inference",async()=>{
 const {image,detectAnimeLandmarks}=await prepare([]);
 await expect(detectAnimeLandmarks(image,new AbortController().signal,true)).resolves.toBeNull();
 expect(mock.run).not.toHaveBeenCalled();
});
it("a detector failure cannot enable the centered-crop editor fallback",async()=>{
 const {image,detectAnimeLandmarks}=await prepare([]);
 mock.face.mockRejectedValue(new Error("face model unavailable"));
 await expect(detectAnimeLandmarks(image,new AbortController().signal,true)).resolves.toBeNull();
 expect(mock.run).not.toHaveBeenCalled();
});
it("a single detected face reaches landmark inference",async()=>{
 const {image,detectAnimeLandmarks}=await prepare([face]);
 vi.spyOn(HTMLCanvasElement.prototype,"getContext").mockReturnValue({
  fillRect:vi.fn(),drawImage:vi.fn(),getImageData:()=>({data:new Uint8ClampedArray(256*256*4)}),
 } as unknown as CanvasRenderingContext2D);
 mock.run.mockRejectedValue(new Error("inference reached"));
 await expect(detectAnimeLandmarks(image,new AbortController().signal,true)).rejects.toThrow("inference reached");
 expect(mock.run).toHaveBeenCalledTimes(1);
});
const heatmaps=(peak:number)=>{const data=new Float32Array(28*64*64);for(let joint=0;joint<28;joint+=1)data[joint*64*64+32*64+32]=peak;return {output:{data}};};
function stubCanvas(){
 vi.spyOn(HTMLCanvasElement.prototype,"getContext").mockReturnValue({
  fillRect:vi.fn(),drawImage:vi.fn(),getImageData:()=>({data:new Uint8ClampedArray(256*256*4)}),
 } as unknown as CanvasRenderingContext2D);
}
it("a false hit the detector prefers loses to the box the landmark model fits better",async()=>{
 const hair={boundingBox:{originX:40,originY:10,width:200,height:200},categories:[{score:.9}]};
 const real={boundingBox:{originX:300,originY:260,width:200,height:200},categories:[{score:.3}]};
 const {image,detectAnimeLandmarks}=await prepare([hair,real]);
 stubCanvas();
 mock.run.mockResolvedValueOnce(heatmaps(.1)).mockResolvedValueOnce(heatmaps(.9));
 const result=await detectAnimeLandmarks(image,new AbortController().signal,true);
 expect(mock.run).toHaveBeenCalledTimes(2);
 expect(result?.debug.faceBox).toMatchObject({x:300,y:260});
});
it("a box the landmark model cannot fit is not accepted",async()=>{
 const {image,detectAnimeLandmarks}=await prepare([face]);
 stubCanvas();
 mock.run.mockResolvedValue(heatmaps(.1));
 await expect(detectAnimeLandmarks(image,new AbortController().signal,true)).resolves.toBeNull();
});
it("two separate faces that both fit well stay ambiguous",async()=>{
 const other={boundingBox:{originX:500,originY:80,width:120,height:150},categories:[{score:.8}]};
 const {image,detectAnimeLandmarks}=await prepare([{...face,boundingBox:{originX:20,originY:80,width:240,height:300}},other]);
 stubCanvas();
 mock.run.mockResolvedValue(heatmaps(.95));
 await expect(detectAnimeLandmarks(image,new AbortController().signal,true)).resolves.toBeNull();
});
