from app.core.config import get_settings
from app.operations.worker import LocalWorker
from .test_api import api
from .test_migrations import migration_database
from .test_operations import quoted,submit_request
from .test_worker import ScriptedRuntime

async def test_dimensions_come_from_immutable_clean_asset_in_all_read_projections(api,tmp_path):
    client,factory,owner,design,branch=api
    operation=await submit_request(factory,owner,branch,await quoted(factory,owner,branch))
    worker=LocalWorker(factory,get_settings().model_copy(update={"v2_runtime_dir":str(tmp_path/"runtime")}),runtime=ScriptedRuntime())
    await worker.run_once()
    snapshot=(await client.get(f"/api/v2/designs/{design}")).json()
    current=snapshot["current_revision"]
    assert (current["width"],current["height"])==(16,16)
    history=(await client.get(f"/api/v2/branches/{branch}/history")).json()
    revision=next(item["revision"] for item in history["items"] if item["type"]=="revision")
    assert revision["id"]==current["id"] and revision["width"]==16 and revision["height"]==16
    candidates=(await client.get(f"/api/v2/revisions/{current['id']}/candidates")).json()
    assert candidates["items"][0]["width"]==16 and candidates["items"][0]["height"]==16
    listing=(await client.get(f"/api/v2/designs/{design}/revisions")).json()
    assert listing["items"][0]["width"]==16 and listing["items"][0]["height"]==16
