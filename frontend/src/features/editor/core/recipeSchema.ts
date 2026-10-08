import {z} from "zod";
import {faceControlRanges,eyeControlRanges,browControlRanges,mouthControlRanges,type EditRecipe,type ControlRange} from "../deformation/recipe";
const finite=z.number().finite(),unit=finite.min(0).max(1),point=z.object({x:unit,y:unit}).strict();
const id=z.string().min(1).max(100),text=z.string().max(10000);
function controls(ranges:Record<string,ControlRange>){
  return z.object(Object.fromEntries(Object.entries(ranges).map(([key,range])=>[key,finite.min(range.min).max(range.max)]))).strict();
}
const eyeRegion=z.object({radiusX:finite.positive().max(4),radiusY:finite.positive().max(4),
  radiusTopY:finite.positive().max(4),radiusBottomY:finite.positive().max(4)}).strict();
// Detected landmarks can lie just outside the image; the renderer clips them. Do not clamp on import.
const landmarkPoint=z.object({x:finite.min(-4).max(4),y:finite.min(-4).max(4)}).strict();
const browLandmark=z.object({inner:landmarkPoint,peak:landmarkPoint,outer:landmarkPoint}).strict();
const shape=z.object({
  imageCoordinates:z.object({version:z.literal(1),width:finite.int().positive().max(16384),height:finite.int().positive().max(16384),
    effectScale:finite.positive().max(16384)}).strict(),
  face:controls(faceControlRanges),eyes:controls(eyeControlRanges),brows:controls(browControlRanges),mouth:controls(mouthControlRanges),
  liquify:z.array(z.object({x:unit,y:unit,radius:finite.positive().max(16384*240),strength:unit,
    mode:z.enum(["expand","shrink","push-left","push-right","push-up","push-down","warp","scale"]),
    deltaX:finite.min(-.35).max(.35).optional(),deltaY:finite.min(-.35).max(.35).optional(),scale:finite.min(-1).max(1).optional(),
  }).strict()).max(10000),
  annotations:z.array(z.object({id,kind:z.enum(["callout","box","pin","arrow","rect","text"]),x:unit,y:unit,
    width:unit,height:unit,endX:unit,endY:unit,note:text,color:z.string().regex(/^#[a-fA-F0-9]{3,8}$/).optional(),
    fontSize:finite.min(1).max(256).optional(),strokeWidth:finite.positive().max(256).optional(),text:text.optional(),
  }).strict()).max(1000),
  details:z.object({preserveSkinTexture:z.boolean(),preserveHairEdges:z.boolean(),protectBangs:z.boolean(),
    protectAccessories:z.boolean(),manualLandmarkEdit:z.boolean(),calibrationMode:z.boolean(),accessoryNote:text,
    refineIntensity:unit,regions:z.array(z.object({id,...point.shape,radius:finite.positive().max(1)}).strict()).max(1000),
  }).strict(),
  landmarks:z.object({leftEye:landmarkPoint,rightEye:landmarkPoint,chin:landmarkPoint,jawLeft:landmarkPoint,jawRight:landmarkPoint,
    mouthCenter:landmarkPoint,mouthLeft:landmarkPoint,mouthRight:landmarkPoint,
    eyeRegions:z.object({left:eyeRegion,right:eyeRegion}).strict().optional(),
    brows:z.object({left:browLandmark,right:browLandmark}).strict().optional(),
  }).strict().optional(),
}).strict();
/** Validate without reordering geometry keys: legacy mask binding strings use the original recipe order. */
export const normalizedRecipeSchema=z.custom<EditRecipe>(value=>shape.safeParse(value).success,"Unsupported editor recipe");

export const annotationGeometrySchema=shape.shape.annotations;
