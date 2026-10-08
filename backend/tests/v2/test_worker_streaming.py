from sqlalchemy import select
from app.design.models import Message, DomainEvent
from .test_worker import prepared, ScriptedRpc
from .test_operations import factory
from .test_migrations import migration_database

async def test_worker_persists_delta_before_final_and_finishes_same_message(factory,tmp_path,monkeypatch):
    original=ScriptedRpc.request
    async def request(self,method,params):
        response=await original(self,method,params)
        if self.images and method=="turn/start":
            self.events.insert(0,{"method":"item/agentMessage/delta","params":{
                "threadId":self.thread,"turnId":self.turn,"itemId":"message","delta":"Which wig color "}})
        return response
    monkeypatch.setattr(ScriptedRpc,"request",request)
    worker,runtime,owner,branch,opid=await prepared(factory,tmp_path,"clarification")
    await worker.run_once()
    async with factory() as db:
        messages=(await db.execute(select(Message).where(Message.operation_id==opid,Message.role=="assistant"))).scalars().all()
        assert len(messages)==1 and messages[0].status=="completed"
        assert messages[0].content_parts==[{"type":"text","text":"Which color?"},{"type":"reply_intent","schema_version":1,"kind":"question"}]
        events=(await db.execute(select(DomainEvent).where(DomainEvent.event_type.in_(["message.delta","message.completed"]))
                                .order_by(DomainEvent.sequence))).scalars().all()
        assert [e.event_type for e in events]==["message.delta","message.completed"]
        assert all(e.payload["message_id"]==messages[0].id for e in events)
