import {afterEach,expect,it,vi} from "vitest";
import {adminRecovery,commandSchema} from "./api";
afterEach(()=>vi.unstubAllGlobals());
it("reads retention receipts without exposing maintenance as a web command",async()=>{
 const items=["expire_uploads","purge_uploads","restore_uploads","retire_runtime","purge_runtime","retire_runtime_orphans","purge_runtime_orphans","configure_rollout","retire_backups","purge_backups","expire_trash_assets","purge_trash_assets","scrub_deleted_drafts","scrub_design_content","scrub_account_content","retire_account_files","purge_account_files","request_account_closure","cancel_account_closure","close_account","retire_native_history","purge_native_history","migrate_minio_assets","retire_minio_orphans","purge_minio_orphans"].map((action,index)=>({
  id:String(index),actor_id:"admin",client_request_id:"digest",action,target_id:"global",
  reason:"Approved policy",units:null,status:"completed",result:{count:1},
  error_code:null,replay_count:0,created_at:"2026-09-28T00:00:00Z",
  started_at:"2026-09-28T00:00:00Z",completed_at:"2026-09-28T00:00:00Z",
 }));
 vi.stubGlobal("fetch",vi.fn().mockResolvedValue({ok:true,json:async()=>({items,has_more:false})}));
 expect((await adminRecovery.actions(0)).items).toEqual(items);
 for(const item of items){
  expect(commandSchema.safeParse({client_request_id:"request-1",action:item.action,
    target_id:"global",reason:"Approved policy"}).success).toBe(false);
 }
});
