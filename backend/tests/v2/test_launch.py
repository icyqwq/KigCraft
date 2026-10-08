from pathlib import Path
import pytest
from app.codex_runtime.launch import local_command, local_environment

def test_launch_does_not_modify_config_and_disables_external_tools(monkeypatch, tmp_path):
    config = '[mcp_servers.test]\ncommand = "secret-command"\n'
    (tmp_path / "config.toml").write_text(config, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    command = local_command("codex", images=False)
    assert "features.image_generation=false" in command
    assert "mcp_servers.test.enabled=false" in command
    assert "features.plugins=false" in command
    assert (tmp_path / "config.toml").read_text(encoding="utf-8") == config
    assert "secret-command" not in repr(command)

def test_child_environment_excludes_application_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "secret")
    monkeypatch.setenv("S3_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("CODEX_APP_TOOLS_PIPE_PATH", "private-pipe")
    env = local_environment()
    assert not {"DATABASE_URL", "S3_SECRET_ACCESS_KEY", "CODEX_APP_TOOLS_PIPE_PATH"} & env.keys()

def test_unknown_dotted_mcp_name_fails_closed(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('[mcp_servers."unsafe.name"]\ncommand="x"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    with pytest.raises(ValueError):
        local_command("codex")


def test_recovery_profile_does_not_inherit_host_mcp_keys(monkeypatch, tmp_path):
    host = tmp_path / "host"; host.mkdir()
    recovery = tmp_path / "recovery"; recovery.mkdir()
    (host / "config.toml").write_text('[mcp_servers.host_only]\ncommand="private-host-tool"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(host))
    command = local_command("codex", images=False, codex_home=recovery)
    assert not any("mcp_servers." in value for value in command)
    assert "features.image_generation=false" in command
    assert not (recovery / "config.toml").exists()
