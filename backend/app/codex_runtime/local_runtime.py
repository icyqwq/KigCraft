"""One Codex login on this machine: a single test account (local) or every account (shared)."""
from __future__ import annotations
import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path
from app.codex_runtime.launch import DESIGN_MODEL, local_command, local_environment
from app.codex_runtime.rpc import CodexRpc, RpcError
from app.codex_runtime.profile_rpc import ProfileRpc
from app.core.config import Settings


class LocalRuntime:
    def __init__(self, settings: Settings):
        shared = settings.v2_runtime_mode == "shared"
        if not shared and (settings.app_env not in {"development", "test"} or settings.v2_runtime_mode != "local"
                or not settings.v2_local_user_id):
            raise ValueError("Local runtime requires a development test account")
        self.settings = settings
        self.profile = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()
        executable = shutil.which(settings.v2_codex_path)
        if not executable:
            raise ValueError("Configured Codex executable not found")
        self.executable = str(Path(executable).resolve())
        self.version = settings.v2_codex_version

    async def verify(self):
        process = await asyncio.create_subprocess_exec(
            self.executable, "--version", stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=self.environment())
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RpcError("version_probe_timeout") from None
        if process.returncode or output.decode("utf-8").strip() != f"codex-cli {self.version}":
            raise RpcError("codex_version_mismatch")
        host_name = "codex-code-mode-host.exe" if os.name == "nt" else "codex-code-mode-host"
        if not Path(self.executable).with_name(host_name).is_file():
            raise RpcError("code_mode_host_missing")

    def connection(self, workspace: Path, *, images: bool) -> CodexRpc:
        workspace.mkdir(parents=True, exist_ok=True)
        return ProfileRpc(local_command(self.executable, images=images, codex_home=self.profile), cwd=workspace,
                          env=self.environment(), timeout=45, profile=self.profile)

    def environment(self):
        env=local_environment()
        env["CODEX_HOME"]=str(self.profile)
        return env

    async def identity(self, rpc: CodexRpc) -> str:
        response = await rpc.request("account/read", {"refreshToken": False})
        account = response.get("account") or {}
        routing = response.get("workspaceRouting") or {}
        # A profile path alone cannot distinguish a changed login.
        if account.get("type") != "chatgpt" or not account.get("email"):
            raise RpcError("local_chatgpt_login_required")
        profile = str(self.profile)
        private_identity = [account["email"].casefold(), routing.get("chatgptAccountId"),
                            profile, self.executable, self.version, DESIGN_MODEL]
        return hashlib.sha256(json.dumps(private_identity, separators=(",", ":")).encode()).hexdigest()
