import {z} from "zod";
import {createLocalEditorDocument,editorStateFromDocument,type SavedEditorDocument,type LocalEditorDocument} from "../core/editorDocument";
import {localEditorDocumentSchema} from "../core/documentSchema";
import type {EditorDraftContent,DraftSource} from "../drafts/types";
import {inspectDraftSource} from "../drafts/repository";
import type {ReferenceAttachment} from "../../references/attachments";

const MAGIC="KIGCRAFT",HEADER_BYTES=16,MAX_MANIFEST=4*1024*1024,MAX_PACKAGE=176*1024*1024,MAX_REFERENCES=40*1024*1024;
const sha=z.string().regex(/^[a-f0-9]{64}$/),integer=z.number().int().nonnegative();
const filename=z.string().min(1).max(200).refine(name=>!/[\\/\x00]/.test(name));
const blobSchema=z.object({id:sha,byteLength:integer.positive().max(128*1024*1024),
  mimeType:z.enum(["image/png","image/jpeg","image/webp"]),width:integer.positive().max(16384),height:integer.positive().max(16384)}).strict();
const attachmentSchema=z.object({key:z.string().min(1).max(100),blobId:sha,name:filename,lastModified:integer,
  usage:z.string().max(10000)}).strict();
const manifestSchema=z.object({
  schema:z.literal("kigcraft.project"),version:z.literal(1),name:filename,rootBlobId:sha,document:localEditorDocumentSchema,
  inputs:z.object({localEditNote:z.string().max(10000),localEditMode:z.enum(["preserve","lock_outside"]).optional(),
    references:z.array(attachmentSchema).max(20),extra:attachmentSchema.nullable(),extraDescription:z.string().max(10000),
  }).strict(),
  blobs:z.array(blobSchema).min(1).max(22),
}).strict().superRefine((manifest,ctx)=>{
  const ids=manifest.blobs.map(item=>item.id),refs=manifest.inputs.references.map(item=>item.key);
  const used=[manifest.rootBlobId,...manifest.inputs.references.map(item=>item.blobId),...(manifest.inputs.extra?[manifest.inputs.extra.blobId]:[])];
  if(new Set(ids).size!==ids.length||new Set(refs).size!==refs.length||used.some(id=>!ids.includes(id))||ids.some(id=>!used.includes(id)))
    ctx.addIssue({code:"custom",message:"Invalid project asset map"});
  const root=manifest.blobs.find(item=>item.id===manifest.rootBlobId);
  if(root&&(root.id!==manifest.document.root.sha256||root.width!==manifest.document.root.width||root.height!==manifest.document.root.height))
    ctx.addIssue({code:"custom",message:"Project root mismatch"});
  const referenceBytes=manifest.inputs.references.reduce((sum,item)=>sum+(manifest.blobs.find(blob=>blob.id===item.blobId)?.byteLength??0),0)+
    (manifest.inputs.extra?manifest.blobs.find(blob=>blob.id===manifest.inputs.extra?.blobId)?.byteLength??0:0);
  if(referenceBytes>MAX_REFERENCES)ctx.addIssue({code:"custom",message:"Project references too large"});
});
export type ProjectCapture={source:DraftSource;content:EditorDraftContent;previous?:SavedEditorDocument};
export type PreparedProject={name:string;source:DraftSource;document:LocalEditorDocument;content:EditorDraftContent};
type ResolveAsset=(id:string,signal:AbortSignal)=>Promise<Blob>;
export const projectMimeType="application/x-kigcraft-project";
export function projectFileName(name:string){return name.replace(/\.[^.]+$/,"").replace(/[\\/\x00]/g,"_").slice(0,180)+".kigcraft";}
function safeName(name:string){return name.replace(/[\\/\x00]/g,"_").slice(0,200)||"image.png";}
function imageType(blob:Blob){if(!["image/png","image/jpeg","image/webp"].includes(blob.type))throw new Error("Unsupported image type");return blob.type as "image/png"|"image/jpeg"|"image/webp";}

export async function exportProject(capture:ProjectCapture,name:string,signal:AbortSignal,resolveAsset?:ResolveAsset):Promise<Blob>{
  // Freeze Files, inputs and source synchronously; never read the live editor after the first await.
  const frozen=structuredClone(capture),localId=crypto.randomUUID(),content=frozen.content;
  signal.throwIfAborted();
  const document=await createLocalEditorDocument(content,{kind:"local",localId,sha256:frozen.source.sha256,
    width:frozen.source.width,height:frozen.source.height});
  if(frozen.previous)document.annotationIntents=document.annotationIntents.map(item=>({...item,
    intent:frozen.previous!.annotationIntents.find(previous=>previous.annotationId===item.annotationId)?.intent??item.intent}));
  if(document.maskDocument.binding)document.maskDocument.binding.sourceKey="local:"+localId;
  const blobs:z.infer<typeof blobSchema>[]=[],bodies:Blob[]=[];
  async function add(blob:Blob,expected?:DraftSource){
    signal.throwIfAborted();
    if(bodies.reduce((sum,item)=>sum+item.size,0)+blob.size>MAX_PACKAGE)throw new Error("Project too large");
    const source=await inspectDraftSource(blob);signal.throwIfAborted();
    if(expected&&(source.sha256!==expected.sha256||source.width!==expected.width||source.height!==expected.height))throw new Error("Root bytes have changed");
    if(!blobs.some(item=>item.id===source.sha256)){blobs.push({id:source.sha256,byteLength:blob.size,mimeType:imageType(blob),width:source.width,height:source.height});bodies.push(blob);}
    return source.sha256;
  }
  const rootBlobId=await add(frozen.source.blob,frozen.source);
  const sourceReferences:ReferenceAttachment[]=content.localReferences??content.selectedLocalReferenceKeys.map(key=>({key,assetId:key,usage:""}));
  if(content.localReferences===undefined&&content.localUploadedReferenceFile)sourceReferences.push({key:"legacy-file",file:content.localUploadedReferenceFile,usage:content.localUploadedReferenceDescription});
  async function reference(item:ReferenceAttachment){
    const blob=item.file??(item.assetId&&resolveAsset?await resolveAsset(item.assetId,signal):undefined);
    if(!blob)throw new Error("A reference file is unavailable");
    return {key:item.key,blobId:await add(blob),usage:item.usage,name:safeName(item.file?.name??"reference.png"),lastModified:item.file?.lastModified??0};
  }
  if(sourceReferences.length>20)throw new Error("Too many project references");
  const references=[];for(const item of sourceReferences)references.push(await reference(item));
  const extra=content.extraReferenceFile?await reference({key:"extra",file:content.extraReferenceFile,usage:content.extraReferenceDescription}):null;
  const manifest=manifestSchema.parse({schema:"kigcraft.project",version:1,name:safeName(name),rootBlobId,document,
    inputs:{localEditNote:content.localEditNote,...(content.localEditMode?{localEditMode:content.localEditMode}:{}),references,extra,extraDescription:content.extraReferenceDescription},blobs});
  const json=new TextEncoder().encode(JSON.stringify(manifest));
  if(json.byteLength>MAX_MANIFEST)throw new Error("Project document too large");
  const header=new Uint8Array(HEADER_BYTES);header.set(new TextEncoder().encode(MAGIC));
  const view=new DataView(header.buffer);view.setUint32(8,1,true);view.setUint32(12,json.byteLength,true);
  const result=new Blob([header,json,...bodies],{type:projectMimeType});
  if(result.size>MAX_PACKAGE)throw new Error("Project too large");signal.throwIfAborted();return result;
}

export async function importProject(file:Blob,signal:AbortSignal):Promise<PreparedProject>{
  if(file.size<HEADER_BYTES||file.size>MAX_PACKAGE)throw new Error("Invalid project size");
  signal.throwIfAborted();
  const bytes=new Uint8Array(await file.slice(0,HEADER_BYTES).arrayBuffer()),view=new DataView(bytes.buffer);
  if(new TextDecoder().decode(bytes.slice(0,8))!==MAGIC||view.getUint32(8,true)!==1)throw new Error("Unsupported project version");
  const length=view.getUint32(12,true);
  if(!length||length>MAX_MANIFEST||HEADER_BYTES+length>file.size)throw new Error("Invalid project manifest length");
  const json=new TextDecoder("utf-8",{fatal:true}).decode(await file.slice(HEADER_BYTES,HEADER_BYTES+length).arrayBuffer());
  const manifest=manifestSchema.parse(JSON.parse(json));
  if(HEADER_BYTES+length+manifest.blobs.reduce((sum,item)=>sum+item.byteLength,0)!==file.size)throw new Error("Project payload length mismatch");
  let offset=HEADER_BYTES+length;const sources=new Map<string,DraftSource>();
  for(const item of manifest.blobs){
    signal.throwIfAborted();
    const blob=file.slice(offset,offset+item.byteLength,item.mimeType);offset+=item.byteLength;
    const source=await inspectDraftSource(blob);
    if(source.sha256!==item.id||source.width!==item.width||source.height!==item.height)throw new Error("Project image integrity mismatch");
    sources.set(item.id,source);
  }
  const document=structuredClone(manifest.document),localId=crypto.randomUUID();
  document.root.localId=localId;
  if(document.maskDocument.binding)document.maskDocument.binding.sourceKey="local:"+localId;
  const inputs=manifest.inputs;
  function restore(item:z.infer<typeof attachmentSchema>){const blob=sources.get(item.blobId)!.blob;
    return new File([blob],item.name,{type:blob.type,lastModified:item.lastModified});}
  const content:EditorDraftContent={...editorStateFromDocument(document),localEditNote:inputs.localEditNote,
    ...(inputs.localEditMode?{localEditMode:inputs.localEditMode}:{}),
    localReferences:inputs.references.map(item=>({key:item.key,usage:item.usage,file:restore(item)})),
    selectedLocalReferenceKeys:[],localUploadedReferenceFile:null,localUploadedReferenceDescription:"",
    extraReferenceFile:inputs.extra?restore(inputs.extra):null,extraReferenceDescription:inputs.extraDescription};
  signal.throwIfAborted();return {name:manifest.name,source:sources.get(manifest.rootBlobId)!,document,content};
}
