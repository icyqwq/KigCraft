import {z} from "zod";
import {designApi,DesignApiError,editorSaveResultSchema,type EditorSaveResult} from "./api";
import {editorSaveRequestSchema,type EditorSaveRequest} from "./editorSaveContracts";
import type {EditorDocument} from "../editor/core/editorDocument";
type Target={ownerId:string;branchId:string;baseRevisionId:string;rootAssetId:string;branchVersion:number;historical:boolean};
const ticketSchema=z.discriminatedUnion("phase",[
  z.object({phase:z.literal("prepared"),attempted:z.boolean(),payload:editorSaveRequestSchema}),
  z.object({phase:z.literal("confirmed"),payload:editorSaveRequestSchema,result:editorSaveResultSchema}),
]);
export type ManualSaveTicket=z.infer<typeof ticketSchema>;
export class ManualEditorSave {
  ticket:ManualSaveTicket|null=null;
  private busy=false;
  readonly key:string;
  constructor(readonly target:Target,private storage:Storage=sessionStorage,private api=designApi){
    this.key="kig-v2-editor-save:"+JSON.stringify([target.ownerId,target.branchId,target.baseRevisionId,target.rootAssetId]);
  }
  load(){
    const raw=this.storage.getItem(this.key);
    if(!raw)return this.ticket=null;
    const value=JSON.parse(raw);
    if(value.schema!==1||value.ownerId!==this.target.ownerId)throw new Error("Unsupported editor save");
    const ticket=ticketSchema.parse(value.ticket),doc=ticket.payload.document;
    if(doc.baseRevision!==this.target.baseRevisionId||doc.root.assetId!==this.target.rootAssetId)throw new Error("Editor save scope mismatch");
    if(ticket.phase==="confirmed"&&(ticket.result.checkpoint.document.root.assetId!==this.target.rootAssetId||
      ticket.result.checkpoint.revision_id!==ticket.result.revision.id))throw new Error("Invalid editor save receipt");
    return this.ticket=ticket;
  }
  private persist(ticket:ManualSaveTicket){
    this.storage.setItem(this.key,JSON.stringify({schema:1,ownerId:this.target.ownerId,ticket}));
    this.ticket=ticket;return ticket;
  }
  async prepare(document:EditorDocument,image:Blob,checkpointId:string|null,signal:AbortSignal,unchangedAssetId?:string){
    if(this.busy||this.load())throw new Error("A previous editor save must be resolved first");
    const frozen=structuredClone(document);
    this.busy=true;signal=AbortSignal.any([signal,AbortSignal.timeout(60000)]);
    try{
      signal.throwIfAborted();
      const asset=unchangedAssetId?{id:unchangedAssetId}:await this.api.upload(image,this.target.ownerId,signal);
      signal.throwIfAborted();
      const payload=editorSaveRequestSchema.parse({client_request_id:crypto.randomUUID(),expected_branch_version:this.target.branchVersion,
        expected_checkpoint_id:checkpointId,clean_asset_id:asset.id,document:frozen,
        ...(this.target.historical?{continuation:{source_revision_id:this.target.baseRevisionId,boundary_message_id:null}}:{})});
      return this.persist({phase:"prepared",attempted:false,payload});
    }finally{this.busy=false;}
  }
  async submit(signal:AbortSignal):Promise<EditorSaveResult|null>{
    if(this.busy)return null;
    const current=this.load();if(!current)return null;
    if(current.phase==="confirmed")return current.result;
    this.busy=true;signal=AbortSignal.any([signal,AbortSignal.timeout(30000)]);
    try{
      signal.throwIfAborted();
      const attempted={...current,attempted:true};this.persist(attempted);
      const result=await this.api.saveEditor(this.target.branchId,attempted.payload,signal);
      this.persist({phase:"confirmed",payload:attempted.payload,result});
      return result;
    }catch(error){
      // Only a definite rejection permits a new snapshot; uncertain acknowledgements keep the exact request.
      if(error instanceof DesignApiError&&error.code!=="idempotency_conflict"&&[400,403,404,409,422].includes(error.status)){
        this.storage.removeItem(this.key);this.ticket=null;
      }
      throw error;
    }finally{this.busy=false;}
  }
  discardUnattempted(){
    if(this.busy||this.ticket?.phase!=="prepared"||this.ticket.attempted)return false;
    this.storage.removeItem(this.key);this.ticket=null;return true;
  }
  acknowledge(){
    if(this.busy||this.ticket?.phase!=="confirmed")return false;
    this.storage.removeItem(this.key);this.ticket=null;return true;
  }
}
