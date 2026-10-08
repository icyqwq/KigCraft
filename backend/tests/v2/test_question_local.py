import pytest
from app.core.config import get_settings
from app.contracts.v2 import InputSnapshot
from app.conversation.questions import saved
from app.design.models import InputRecord,Operation
from .test_operations import factory,quoted,submit_request
from .test_migrations import migration_database
from .test_local_edit import accepted
from .test_questions import answering

async def test_local_answer_retains_mask_recipe_and_original_asset_hashes(factory,tmp_path,monkeypatch):
    monkeypatch.setenv("V2_LOCAL_EDIT_ENABLED","true")
    monkeypatch.setenv("V2_STORAGE_DIR",str(tmp_path/"assets"))
    get_settings.cache_clear()
    try:
        worker,runtime,owner,branch,base,opid,_=await accepted(factory,tmp_path,monkeypatch,mode="preserve",runtime_mode="clarification")
        await worker.run_once()
        async with factory() as db:
            op=await db.get(Operation,opid)
            value=saved(op)
            original=InputSnapshot.model_validate((await db.get(InputRecord,op.input_snapshot_id)).snapshot)
        payload=await quoted(factory,owner,branch,answering(opid,value))
        answer_id=await submit_request(factory,owner,branch,payload)
        async with factory() as db:
            answer=await db.get(Operation,answer_id)
            inputs=InputSnapshot.model_validate((await db.get(InputRecord,answer.input_snapshot_id)).snapshot)
            assert answer.kind=="local_edit" and inputs.local_edit_mode=="preserve"
            assert inputs.prepared_edit==original.prepared_edit
            assert inputs.references==original.references
            assert inputs.annotation_review()==original.annotation_review()
            assert inputs.user_text=="Coral, please"
    finally:
        get_settings.cache_clear()
