"""Local development launch policy; production isolation is a separate boundary."""
from __future__ import annotations
import os
import re
import tomllib
from pathlib import Path

# Generation model. V2_DESIGN_MODEL overrides it for an account that is not entitled to the default.
DESIGN_MODEL = os.environ.get("V2_DESIGN_MODEL", "").strip() or "gpt-6.1-sol"
# Reasoning effort of every turn, design and review (medium). The binding check compares only the model.
DESIGN_EFFORT = "medium"
TESTED_CODEX_VERSION = "0.155.0-alpha.16.4"


def local_command(codex_path: str, *, images: bool = True, codex_home: Path | None = None) -> list[str]:
    command = [codex_path, "app-server"]
    overrides = {
        "features.shell_tool": "false",
        "features.apps": "false",
        "features.plugins": "false",
        "features.hooks": "false",
        "features.multi_agent": "false",
        "features.browser_use": "false",
        "features.computer_use": "false",
        "features.skip_host_skill_discovery": "true",
        "features.image_generation": str(images).lower(),
        "features.code_mode_host": str(images).lower(),
        "features.view_image": str(images).lower(),
        "web_search": '"disabled"',
    }
    # Read overrides from the same profile the child will open; injecting host MCP
    # keys into an empty recovery profile creates incomplete server definitions.
    config_path = (codex_home or Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))) / "config.toml"
    if config_path.exists():
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        for name in config.get("mcp_servers", {}):
            # CLI dotted-key parsing does not implement quoted TOML segments.
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise ValueError("Unsupported MCP name in local launch configuration")
            overrides[f"mcp_servers.{name}.enabled"] = "false"
    for key, value in overrides.items():
        command.extend(["-c", f"{key}={value}"])
    return command


def local_environment() -> dict[str, str]:
    # Do not pass application DB/S3/mail secrets or desktop control pipes to the model host.
    allowed = {
        "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
        "USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES",
        "PROGRAMFILES(X86)", "PROGRAMDATA", "CODEX_HOME", "CODEX_MCP_NODE_PATH", "HTTP_PROXY", "HTTPS_PROXY",
        "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    }
    env = {k: v for k, v in os.environ.items() if k.upper() in allowed}
    env["PYTHONIOENCODING"] = "utf-8"
    return env
