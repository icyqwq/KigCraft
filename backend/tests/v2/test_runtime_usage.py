import copy
import pytest
from app.codex_runtime.usage import token_snapshot
from app.codex_runtime.rpc import RpcClosed
from .test_session import FakeRpc,session,completed,item

def usage(n=10):
    b={"inputTokens":n,"cachedInputTokens":2,"outputTokens":3,"reasoningOutputTokens":1,"totalTokens":n+3}
    return {"last":dict(b),"total":dict(b),"modelContextWindow":1000}

def event(value,thread="thread",turn="turn"):
    return {"method":"thread/tokenUsage/updated","params":{"threadId":thread,"turnId":turn,"tokenUsage":value}}

@pytest.mark.parametrize("bad",[None,{},True,-1,"12",1.5,9007199254740992])
def test_invalid_usage_stays_unknown_instead_of_zero(bad):
    v=usage();v["last"]["inputTokens"]=bad
    assert token_snapshot(v)=={"status":"invalid","snapshot":None}

def test_whitelist_preserves_explicit_zero_and_missing_optional_unknown():
    v=usage(0);v["last"]["cachedInputTokens"]=0;v["secret"]="private";v["last"]["prompt"]="private"
    parsed=token_snapshot(v)
    assert parsed["status"]=="observed"
    assert parsed["snapshot"]["last"]["inputTokens"]==0
    assert parsed["snapshot"]["last"]["cacheWriteInputTokens"] is None
    assert "private" not in repr(parsed)

async def test_scoped_snapshots_are_replacements_not_summed_and_no_content_leaks():
    observed=[]
    async def collect(v):observed.append(copy.deepcopy(v))
    image=item("imageGeneration",status="completed",result="private-bytes")
    rpc=FakeRpc([{"turn":{"id":"turn"}}],[event(usage(90),thread="foreign"),event(usage(90),turn="old"),
        event(usage()),event(usage()),event(usage(20)),image,image,item("agentMessage",text="private-message"),completed()])
    result=await session(rpc).run([],on_observation=collect)
    assert result.status=="completed"
    assert [v["kind"] for v in observed]==["dispatch","started","usage","usage","usage","image","image_duplicate","completed"]
    assert observed[-4]["token_usage"]["total"]["inputTokens"]==20
    assert "private" not in repr(observed)

async def test_invalid_usage_does_not_fail_completed_turn_or_forward_raw_diagnostic():
    observed=[]
    async def collect(v):observed.append(v)
    rpc=FakeRpc([{"turn":{"id":"turn"}}],[event({"private":"secret"}),completed()])
    assert (await session(rpc).run([],on_observation=collect)).status=="completed"
    assert observed[2]["token_usage"] is None
    assert "secret" not in repr(observed)

async def test_lost_start_ack_only_observes_dispatch_and_does_not_retry():
    observed=[]
    async def collect(v):observed.append(v)
    rpc=FakeRpc([RpcClosed()])
    client=session(rpc)
    with pytest.raises(RpcClosed):await client.run([],on_observation=collect)
    assert observed==[{"kind":"dispatch","thread_id":"thread"}]
    assert client.needs_reconciliation
    assert len(rpc.calls)==1

async def test_failed_observation_prevents_untracked_dispatch():
    async def fail(v):raise OSError("database unavailable")
    rpc=FakeRpc()
    with pytest.raises(OSError):await session(rpc).run([],on_observation=fail)
    assert rpc.calls==[]
