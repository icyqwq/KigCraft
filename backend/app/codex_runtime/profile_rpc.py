"""Keep profile exclusion through handshake, RPC lifetime and confirmed process exit."""
from app.codex_runtime.profile_lock import ProfileLease
from app.codex_runtime.rpc import CodexRpc


class ProfileRpc(CodexRpc):
    def __init__(self,*args,profile,**kwargs):
        super().__init__(*args,**kwargs)
        self.profile=profile
        self._profile_lease=None
        self._spawn_failed=False

    async def start(self):
        if self._process is not None or self._closed.is_set():
            raise RuntimeError("RPC instances cannot be restarted")
        self._profile_lease=ProfileLease(self.profile).acquire()
        try:
            result=await super().start()
            self._profile_lease.running(self._process.pid)
            return result
        except OSError:
            # OS spawn failure with no process handle is a definite non-start.
            self._spawn_failed=self._process is None
            await self.close()
            raise
        except BaseException:
            await self.close()
            raise

    async def close(self):
        try:
            await super().close()
        finally:
            lease=self._profile_lease
            self._profile_lease=None
            if lease is not None:
                exited=self._process is not None and self._process.returncode is not None
                # Cancellation while create_subprocess_exec is pending may leave
                # no handle and an unknown process. Keep the opening marker.
                lease.release(clean=exited or self._spawn_failed)
