import pytest
from app.codex_runtime import session as runtime_session
from app.codex_runtime.rpc import RpcError
from .test_session import FakeRpc, session, item, completed
from app.codex_runtime.session import text_input

def delta(text, item_id="first", **extra):
    return {"method":"item/agentMessage/delta", "params":{
        "threadId":"thread","turnId":"turn","itemId":item_id,"delta":text,**extra}}

async def test_stream_whitelists_scopes_and_preserves_each_item(monkeypatch):
    ticks=iter([1.,1.1,1.3,1.6])
    monkeypatch.setattr(runtime_session,"monotonic",lambda:next(ticks))
    rpc=FakeRpc([{"turn":{"id":"turn"}}],[
        delta("other thread",threadId="other"),delta("other turn",turnId="other"),
        {"method":"item/reasoning/textDelta","params":{"threadId":"thread","turnId":"turn","delta":"private"}},
        delta("Hello "),delta("coral "),delta("wig "),delta("Second reply ","second"),
        item("agentMessage",id="first",text="Hello coral wig.",phase="commentary"),
        delta("late",item_id="first"),
        item("agentMessage",id="second",text="Second reply.",phase="final_answer"),completed()])
    observed=[]
    async def partial(turn,value):observed.append((turn,value.copy()))
    result=await session(rpc).run([text_input("design")],on_message_delta=partial)
    assert observed==[
        ("turn",{"id":"first","text":"Hello "}),
        ("turn",{"id":"first","text":"Hello coral wig "}),
        ("turn",{"id":"second","text":"Second reply "})]
    assert [m["id"] for m in result.messages]==["first","second"]
    assert all("private" not in str(value) for value in observed)

async def test_oversize_partial_stops_publication_but_complete_item_remains_authoritative(monkeypatch):
    monkeypatch.setattr(runtime_session,"monotonic",lambda:100)
    rpc=FakeRpc([{"turn":{"id":"turn"}}],[delta("x"*64001),delta("more"),item("agentMessage",id="first",text="Final."),completed()])
    observed=[]
    async def partial(turn,value):observed.append(value)
    result=await session(rpc).run([],on_message_delta=partial)
    assert observed==[] and result.messages[0]["text"]=="Final."

@pytest.mark.parametrize("value",[delta(7),delta("text",item_id=None)])
async def test_invalid_stream_identity_fails_with_reconciliation_required(value):
    rpc=FakeRpc([{"turn":{"id":"turn"}}],[value])
    client=session(rpc)
    async def partial(turn,value):pass
    with pytest.raises(RpcError,match="invalid_agent_message_delta"):
        await client.run([],on_message_delta=partial)
    assert client.needs_reconciliation
