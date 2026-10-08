"""Linux Docker runtime for one explicitly provisioned account; no automatic fallback."""
from __future__ import annotations
import asyncio
from contextlib import asynccontextmanager, suppress
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from app.codex_runtime.launch import DESIGN_MODEL, local_command
from app.codex_runtime.profile_lock import ProfileLease, check_components
from app.codex_runtime.rpc import CodexRpc, RpcError
from app.codex_runtime.selection import container_spec, worker_owner


class DockerEngine:
    """Local daemon only; no inherited contexts, credentials, proxies or remote endpoints."""
    def __init__(self, executable, socket_path, config_dir):
        self.executable = executable
        self.socket_path = socket_path
        self.config_dir = Path(config_dir)
        if not Path(executable).is_absolute() or not Path(executable).is_file():
            raise RpcError("docker_executable_unavailable")
        if not Path(socket_path).is_absolute() or any(c in socket_path for c in "\n\r\x00"):
            raise RpcError("local_docker_socket_required")
        check_components(self.config_dir)
        self.config_dir.mkdir(mode=0o700, parents=False, exist_ok=True)
        if any(self.config_dir.iterdir()):
            raise RpcError("empty_docker_configuration_required")
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(self.config_dir), "LANG": "C.UTF-8"}

    def command(self, *args):
        return [self.executable, "--host", "unix://" + self.socket_path, "--config", str(self.config_dir), *args]

    async def run(self, *args, timeout=30):
        process = None
        try:
            async with asyncio.timeout(timeout):
                process = await asyncio.create_subprocess_exec(*self.command(*args), env=self.env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                result = await process.stdout.read(1024 * 1024 + 1)
                if len(result) > 1024 * 1024:
                    raise RpcError("docker_output_limit")
                # read(n) may return short before EOF; gather bounded chunks.
                while chunk := await process.stdout.read(1024 * 1024 + 1 - len(result)):
                    result += chunk
                    if len(result) > 1024 * 1024:
                        raise RpcError("docker_output_limit")
                await process.wait()
                if process.returncode:
                    raise RpcError("docker_command_failed")
                return result.decode("utf-8").strip()
        except asyncio.CancelledError:
            raise
        except RpcError:
            raise
        except Exception:
            raise RpcError("docker_unavailable") from None
        finally:
            if process is not None and process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def inspect(self, kind, identity):
        try:
            rows = json.loads(await self.run(kind, "inspect", identity))
            if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
                raise ValueError()
            return rows[0]
        except (ValueError, TypeError):
            raise RpcError("docker_inspection_unavailable") from None


class ContainerRuntime:
    def __init__(self, settings, *, engine=None):
        if settings.v2_runtime_mode != "container":
            raise RpcError("container_runtime_required")
        if engine is None and sys.platform != "linux":
            raise RpcError("linux_container_worker_required")
        self.settings = settings
        self.spec = container_spec(settings, worker_owner(settings))
        self.version = settings.v2_codex_version
        if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:-[a-z0-9]+(?:\.[a-z0-9]+)*)?", self.version):
            raise RpcError("invalid_codex_version")
        self.profile = Path(self.spec.profile)
        self.work_root = Path(self.spec.work)
        self.input_root = Path(self.spec.inputs)
        self._lease = None
        self._active = False
        self._uncertain = False
        self._runtime_id = None
        self.engine = engine
        self.daemon_id = None
        if engine is None:
            self.check_layout()
            self.engine = DockerEngine(settings.v2_docker_path, settings.v2_docker_socket,
                                       Path(self.spec.base) / ".docker-client")

    def check_layout(self):
        # Newly prepared 0700 workspaces must be accessible to the container user.
        if os.geteuid() != self.spec.uid or os.getegid() != self.spec.gid:
            raise RpcError("container_worker_uid_mismatch")
        # Provision directories explicitly. Never synthesize a profile from the host login.
        for path in (Path(self.spec.base), self.profile.parent, self.profile, self.work_root, self.input_root):
            check_components(path)
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077:
                raise RpcError("private_container_directories_required")
        for path in (self.profile, self.work_root, self.input_root):
            info = path.stat()
            if info.st_uid != self.spec.uid or info.st_gid != self.spec.gid:
                raise RpcError("container_directory_owner_mismatch")

    def workspace(self, operation_id):
        from uuid import UUID
        if str(UUID(operation_id)) != operation_id:
            raise RpcError("invalid_operation_workspace")
        path = self.work_root / operation_id
        self.check_workspace(path)
        return path

    def input_workspace(self, operation_id):
        from uuid import UUID
        if str(UUID(operation_id)) != operation_id:
            raise RpcError("invalid_operation_workspace")
        path = self.input_root / operation_id
        check_components(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        return path

    def check_workspace(self, path):
        path = Path(path)
        try:
            relative = path.relative_to(self.work_root)
        except ValueError:
            raise RpcError("container_workspace_outside_owner") from None
        if not relative.parts or any(p in {"..", "."} for p in relative.parts):
            raise RpcError("container_workspace_outside_owner")
        check_components(path)
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        check_components(path)
        if not path.resolve().is_relative_to(self.work_root.resolve()):
            raise RpcError("container_workspace_outside_owner")

    async def inspect_resources(self):
        try:
            info = json.loads(await self.engine.run("info", "--format", "{{json .}}"))
            major = int(info["ServerVersion"].split(".")[0])
            identity = info["ID"]
            if info["OSType"] != "linux" or major < 28 or not isinstance(identity, str) or not 1 <= len(identity) <= 128:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise RpcError("unsupported_container_engine") from None
        if self.daemon_id is not None and self.daemon_id != identity:
            raise RpcError("container_engine_changed")
        self.daemon_id = identity
        runtime = await self.engine.inspect("container", self.spec.runtime)
        proxy = await self.engine.inspect("container", self.spec.proxy)
        self.spec.inspect_container(runtime, "runtime")
        self.spec.inspect_container(proxy, "egress")
        for value in (runtime, proxy):
            if not re.fullmatch("[0-9a-f]{64}", value.get("Id", "")):
                raise RpcError("invalid_container_identity")
        for internal, name in ((True, self.spec.internal), (False, self.spec.external)):
            network = await self.engine.inspect("network", name)
            self.spec.inspect_network(network, runtime["Id"], proxy["Id"], internal=internal)
            for value in (runtime, proxy) if internal else (proxy,):
                endpoint = value["NetworkSettings"]["Networks"][name]
                network_id = endpoint.get("NetworkID")
                unstarted = value is runtime and self.stopped(runtime) and not network_id
                if (network_id != network.get("Id") and not unstarted) or not network.get("Id"):
                    raise RpcError("container_network_changed")
            if internal:
                if "egress" not in proxy["NetworkSettings"]["Networks"][name].get("Aliases", []):
                    raise RpcError("container_proxy_alias_missing")
                if runtime.get("State", {}).get("Running") and runtime["Id"] not in network.get("Containers", {}):
                    raise RpcError("container_network_changed")
        return runtime

    @staticmethod
    def stopped(value):
        state = value.get("State", {})
        return (state.get("Running") is False and state.get("Restarting") is False
                and state.get("Pid") == 0 and state.get("Status") in {"created", "exited"})

    @asynccontextmanager
    async def operation(self):
        if self._lease is not None:
            raise RpcError("container_scope_busy")
        self.check_layout()
        self._lease = ProfileLease(self.profile).acquire()
        self._uncertain = True
        try:
            value = await self.inspect_resources()
            if not self.stopped(value):
                raise RpcError("container_already_running")
            self._runtime_id = value["Id"]
            self._uncertain = False
            yield
        finally:
            lease, self._lease = self._lease, None
            if lease:
                lease.release(clean=not self._uncertain and not self._active)
            self._runtime_id = None

    async def verify_stopped(self):
        """Read-only maintenance check while the operation scope holds the profile lock."""
        if self._lease is None or self._runtime_id is None:
            raise RpcError("container_scope_required")
        self._uncertain = True
        value = await self.inspect_resources()
        if value["Id"] != self._runtime_id or not self.stopped(value):
            raise RpcError("container_stop_unconfirmed")
        self._uncertain = False

    def connection(self, workspace, *, images):
        return ContainerRpc(self, workspace, images)

    async def verify(self):
        # Version probe only. The scope verifies configuration and stops the whole
        # container before release; it never initializes a model session.
        async with self.operation():
            try:
                await self.start_container()
            finally:
                if self._uncertain:
                    await self.stop_container()

    async def start_container(self):
        self._uncertain = True
        await self.engine.run("container", "start", self._runtime_id)
        current = await self.inspect_resources()
        if current["Id"] != self._runtime_id or not current.get("State", {}).get("Running"):
            raise RpcError("container_start_unconfirmed")
        actual = await self.engine.run("container", "exec", self._runtime_id, "/opt/codex/codex", "--version")
        if actual != "codex-cli " + self.version:
            raise RpcError("codex_version_mismatch")
        await self.engine.run("container", "exec", self._runtime_id, "/usr/local/bin/python", "-c",
            "import os; assert all(os.access(p,os.X_OK) for p in ['/opt/codex/codex-code-mode-host','/opt/codex/node'])")

    async def stop_container(self):
        if self._runtime_id is None:
            return
        await self.engine.run("container", "stop", "--time", "10", self._runtime_id)
        value = await self.engine.inspect("container", self._runtime_id)
        if value.get("Id") != self._runtime_id or not self.stopped(value):
            raise RpcError("container_stop_unconfirmed")
        self._uncertain = False

    async def identity(self, rpc):
        response = await rpc.request("account/read", {"refreshToken": False})
        account = response.get("account") or {}
        routing = response.get("workspaceRouting") or {}
        if account.get("type") != "chatgpt" or not account.get("email"):
            raise RpcError("container_chatgpt_login_required")
        identity = [account["email"].casefold(), routing.get("chatgptAccountId"),
            self.daemon_id, self.spec.owner, self.spec.profile, self.spec.image, self.spec.egress_image,
            self.spec.hosts, self.version, DESIGN_MODEL]
        return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


class ContainerRpc(CodexRpc):
    def __init__(self, runtime, workspace, images):
        self.runtime = runtime
        self.images = images
        self.requested_workspace = Path(workspace)
        self._scope = None
        self._owned_scope = False
        self._started_container = False
        self._cleaned = False
        # Filled after container identity and configuration have been inspected.
        super().__init__(runtime.engine.command("container", "exec", runtime.spec.runtime, "/opt/codex/codex", "app-server"), cwd=Path(workspace), env=runtime.engine.env, timeout=45)

    async def start(self):
        if self._process is not None or self._closed.is_set() or self._cleaned:
            raise RuntimeError("RPC instances cannot be restarted")
        if self.runtime._active:
            raise RpcError("container_connection_busy")
        self.runtime._active = True
        try:
            if self.runtime._lease is None:
                self._scope = self.runtime.operation()
                await self._scope.__aenter__()
                self._owned_scope = True
            self.runtime.check_workspace(self.requested_workspace)
            config = self.runtime.profile / "config.toml"
            check_components(config)
            if config.exists() and (not config.is_file() or config.stat().st_nlink != 1 or config.stat().st_size > 65536):
                raise RpcError("container_profile_config_invalid")
            command = local_command("/opt/codex/codex", images=self.images, codex_home=self.runtime.profile)
            self._started_container = True
            await self.runtime.start_container()
            self.command = tuple(self.runtime.engine.command("container", "exec", "--interactive",
                "--workdir", str(self.cwd), self.runtime._runtime_id, *command))
            return await super().start()
        except BaseException:
            await self.close()
            raise

    async def close(self):
        if self._cleaned:
            return
        self._cleaned = True
        try:
            try:
                await super().close()
            finally:
                if self._started_container:
                    await self.runtime.stop_container()
        finally:
            self.runtime._active = False
            if self._owned_scope:
                self._owned_scope = False
                await self._scope.__aexit__(None, None, None)
