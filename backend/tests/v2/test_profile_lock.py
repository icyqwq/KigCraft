import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from app.codex_runtime.profile_lock import ProfileLease, ProfileLockError, ProcessState, process_state
from app.codex_runtime.profile_rpc import ProfileRpc
from app.codex_runtime.local_runtime import LocalRuntime
from app.core.config import Settings

PEER=Path(__file__).parent/"fixtures"/"rpc_lifecycle_server.py"

def test_same_profile_excludes_other_holders_but_other_profiles_work(tmp_path):
    profile=tmp_path/"profile-a"
    first=ProfileLease(profile).acquire()
    try:
        with pytest.raises(ProfileLockError,match="runtime_profile_busy"):
            ProfileLease(profile).acquire()
        other=ProfileLease(tmp_path/"profile-b").acquire()
        other.release(clean=True)
        assert not os.get_inheritable(first.fd)
    finally:
        first.release(clean=True)
    again=ProfileLease(profile).acquire()
    again.release(clean=True)

def test_inspection_does_not_poison_a_new_lock(tmp_path):
    profile=tmp_path/"profile"
    assert ProfileLease(profile).inspect()["state"]=="closed"
    lease=ProfileLease(profile).acquire()
    lease.release(clean=True)

def test_live_orphan_blocks_replacement_and_reset(tmp_path):
    child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"],
                           stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        profile=tmp_path/"profile"
        lease=ProfileLease(profile).acquire()
        lease.running(child.pid)
        lease.release(clean=False)
        with pytest.raises(ProfileLockError,match="runtime_profile_busy"):
            ProfileLease(profile).acquire()
        with pytest.raises(ProfileLockError,match="runtime_profile_busy"):
            ProfileLease(profile).reset(processes_stopped=True)
        assert child.poll() is None
    finally:
        child.terminate()
        child.wait(timeout=5)
    # Kernel confirms the previously recorded child has exited.
    lease=ProfileLease(profile).acquire()
    lease.release(clean=True)

def test_cross_process_holder_and_crash_window(tmp_path):
    profile=tmp_path/"profile"
    source=("import sys; from app.codex_runtime.profile_lock import ProfileLease; "
            "lease=ProfileLease(sys.argv[1]).acquire(); print('ready',flush=True); sys.stdin.readline()")
    child=subprocess.Popen([sys.executable,"-u","-c",source,str(profile)],
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=="ready"
        with pytest.raises(ProfileLockError,match="runtime_profile_busy"):
            ProfileLease(profile).acquire()
    finally:
        child.communicate("\n",timeout=5)
    # Opening without a durable child identity is uncertain even after parent exit.
    with pytest.raises(ProfileLockError,match="runtime_profile_uncertain"):
        ProfileLease(profile).acquire()
    with pytest.raises(ProfileLockError,match="stop_confirmation"):
        ProfileLease(profile).reset()
    ProfileLease(profile).reset(processes_stopped=True)
    lease=ProfileLease(profile).acquire()
    lease.release(clean=True)

def test_pid_reuse_with_different_creation_identity_is_not_an_old_process(tmp_path,monkeypatch):
    from app.codex_runtime import profile_lock
    lease=ProfileLease(tmp_path/"profile").acquire()
    monkeypatch.setattr(profile_lock,"process_state",lambda _:ProcessState(True,"old"))
    lease.running(42)
    lease.release(clean=False)
    monkeypatch.setattr(profile_lock,"process_state",lambda _:ProcessState(True,"new"))
    replacement=ProfileLease(tmp_path/"profile").acquire()
    replacement.release(clean=True)

@pytest.mark.parametrize("raw",[b"",b"{",b'{"version":2}',b"[]" ,b"x"*1025])
def test_corrupt_marker_refuses_replacement_until_explicit_reset(tmp_path,raw):
    lease=ProfileLease(tmp_path/"profile")
    lease.inspect()
    lease.path.write_bytes(b"\0"+raw)
    with pytest.raises(ProfileLockError,match="runtime_profile_uncertain"):
        ProfileLease(tmp_path/"profile").acquire()
    lease.reset(processes_stopped=True)
    assert lease.inspect()["state"]=="closed"

def test_hardlinked_lock_cannot_be_used(tmp_path):
    lease=ProfileLease(tmp_path/"profile")
    lease.inspect()
    os.link(lease.path,tmp_path/"alias")
    with pytest.raises(ProfileLockError,match="path_unsafe"):
        lease.acquire()

async def test_rpc_holds_profile_until_child_has_exited(tmp_path):
    profile=tmp_path/"profile"
    rpc=ProfileRpc([sys.executable,"-u",str(PEER)],cwd=tmp_path,profile=profile,timeout=2)
    async with rpc:
        assert await rpc.request("echo",{})=={"ok":True}
        assert process_state(rpc._process.pid).alive
        with pytest.raises(ProfileLockError,match="runtime_profile_busy"):
            ProfileLease(profile).acquire()
    assert rpc._process.returncode is not None
    lease=ProfileLease(profile).acquire()
    lease.release(clean=True)

async def test_definite_spawn_failure_releases_profile(tmp_path):
    profile=tmp_path/"profile"
    rpc=ProfileRpc([str(tmp_path/"missing-executable")],cwd=tmp_path,profile=profile,timeout=1)
    with pytest.raises(OSError):
        await rpc.start()
    lease=ProfileLease(profile).acquire()
    lease.release(clean=True)

async def test_cancelled_unknown_spawn_keeps_uncertain_marker(tmp_path,monkeypatch):
    entered=asyncio.Event()
    async def pending(*args,**kwargs):
        entered.set()
        await asyncio.Future()
    monkeypatch.setattr(asyncio,"create_subprocess_exec",pending)
    profile=tmp_path/"profile"
    rpc=ProfileRpc(["unused"],cwd=tmp_path,profile=profile,timeout=1)
    task=asyncio.create_task(rpc.start())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProfileLockError,match="runtime_profile_uncertain"):
        ProfileLease(profile).acquire()
    ProfileLease(profile).reset(processes_stopped=True)

async def test_local_runtime_freezes_an_absolute_profile_for_identity_config_and_child(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    home=tmp_path/"profile"
    home.mkdir()
    (home/"config.toml").write_text('[mcp_servers.private]\ncommand="do-not-display"\n',encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME","profile")
    settings=Settings(_env_file=None,v2_runtime_mode="local",v2_local_user_id="test-owner",
                      v2_codex_path=sys.executable,app_env="test")
    runtime=LocalRuntime(settings)
    class Account:
        async def request(self,*args):
            return {"account":{"type":"chatgpt","email":"fixture@example.invalid"},"workspaceRouting":{"chatgptAccountId":"fixture"}}
    identity=await runtime.identity(Account())
    monkeypatch.setenv("CODEX_HOME",str(tmp_path/"other"))
    rpc=runtime.connection(tmp_path/"workspace",images=False)
    assert isinstance(rpc,ProfileRpc)
    assert rpc.profile==home and rpc.env["CODEX_HOME"]==str(home)
    assert "mcp_servers.private.enabled=false" in rpc.command
    assert "do-not-display" not in repr(rpc.command)
    assert await runtime.identity(Account())==identity

def test_process_probe_does_not_signal_or_terminate_current_process():
    result=process_state(os.getpid())
    assert result.alive
    if os.name=="nt":
        assert result.token

async def test_failed_handshake_closes_child_before_releasing_profile(tmp_path):
    source=("import sys,json; f=json.loads(sys.stdin.readline()); "
            "print(json.dumps({'id':f['id'],'error':{'code':-32001,'message':'private'}}),flush=True); "
            "sys.stdin.read()")
    profile=tmp_path/"profile"
    rpc=ProfileRpc([sys.executable,"-u","-c",source],cwd=tmp_path,profile=profile,timeout=1)
    from app.codex_runtime.rpc import RpcError
    with pytest.raises(RpcError):
        await rpc.start()
    assert rpc._process.returncode is not None
    lease=ProfileLease(profile).acquire()
    lease.release(clean=True)
