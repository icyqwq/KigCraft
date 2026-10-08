from types import SimpleNamespace
from sqlalchemy import delete
from app.db.models import User
from app.design.models import DomainEvent
from app.design import router
from app.design.service import event
from .test_operations import factory, setup
from .test_migrations import migration_database


class Request:
    headers={}
    def __init__(self):
        self.state = SimpleNamespace()
    async def is_disconnected(self):
        return False


async def test_retained_event_gap_and_fully_pruned_tail_require_snapshot(factory, monkeypatch):
    owner, design, branch=await setup(factory)
    async with factory.begin() as db:
        await event(db,design,"message.delta",{"message_id":"message","text":"retained"})
        await event(db,design,"message.completed",{"message_id":"message"})
        user=await db.get(User,owner)
    monkeypatch.setattr(router,"get_sessionmaker",lambda:factory)
    async def auth(request,db):
        return user,None
    monkeypatch.setattr(router,"get_current_user_and_session",auth)
    async with factory.begin() as db:
        await db.execute(delete(DomainEvent).where(DomainEvent.design_id==design,DomainEvent.sequence==1))
    response=await router.events(design,Request(),after=0,current=user)
    assert await anext(response.body_iterator)=="event: resync_required\ndata: {}\n\n"
    await response.body_iterator.aclose()
    async with factory.begin() as db:
        await db.execute(delete(DomainEvent).where(DomainEvent.design_id==design))
    response=await router.events(design,Request(),after=0,current=user)
    assert await anext(response.body_iterator)=="event: resync_required\ndata: {}\n\n"
    await response.body_iterator.aclose()
