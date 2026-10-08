from uuid import uuid4

import asyncio
import pytest

from app.auth.admin_cli import create_or_update_admin
from app.core.config import get_settings


async def login_admin(async_client, email: str):
    await create_or_update_admin(email, "AdminPassphrase7!")
    response = await async_client.post(
        "/api/auth/login/password",
        json={"email": email, "password": "AdminPassphrase7!"},
    )
    assert response.status_code == 200


@pytest.mark.anyio
async def test_list_codex_profiles_includes_builtin_default_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    default_home = tmp_path / "default-home"
    default_home.mkdir()
    (default_home / "auth.json").write_text("{\"token\":\"default\"}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(default_home))

    response = await async_client.get("/api/admin/codex-profiles")

    assert response.status_code == 200
    profiles = response.json()["profiles"]
    assert profiles[0]["id"] == "__default__"
    assert profiles[0]["name"] == "默认账号"
    assert profiles[0]["is_builtin"] is True
    assert profiles[0]["is_active"] is True
    assert profiles[0]["credential_files"] == ["auth.json"]


@pytest.mark.anyio
async def test_activate_default_codex_profile_deactivates_managed_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    default_home = tmp_path / "default-home"
    default_home.mkdir()
    (default_home / "auth.json").write_text("{\"token\":\"default\"}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(default_home))

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-a", "name": "Account A"},
    )
    assert create.status_code == 200

    imported = await async_client.put(
        "/api/admin/codex-profiles/account-a/credentials",
        json={"files": {"auth.json": "{\"token\":\"secret\"}"}},
    )
    assert imported.status_code == 200

    activate = await async_client.post("/api/admin/codex-profiles/account-a/activate")
    assert activate.status_code == 200

    switched = await async_client.post("/api/admin/codex-profiles/__default__/activate")
    assert switched.status_code == 200
    assert switched.json()["id"] == "__default__"
    assert switched.json()["is_active"] is True

    from app.codex_profiles.service import active_codex_profile_env

    assert active_codex_profile_env() == {}


@pytest.mark.anyio
async def test_admin_can_create_import_and_activate_codex_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-a", "name": "Account A"},
    )
    assert create.status_code == 200
    assert create.json()["id"] == "account-a"

    imported = await async_client.put(
        "/api/admin/codex-profiles/account-a/credentials",
        json={"files": {"auth.json": "{\"token\":\"secret\"}"}},
    )
    assert imported.status_code == 200
    assert "secret" not in imported.text
    assert imported.json()["credential_files"] == ["auth.json"]

    active = await async_client.post("/api/admin/codex-profiles/account-a/activate")
    assert active.status_code == 200
    assert active.json()["is_active"] is True

    from app.codex_profiles.service import active_codex_profile_env

    env = active_codex_profile_env()
    assert env["CODEX_HOME"].endswith("account-a")


@pytest.mark.anyio
async def test_admin_can_delete_codex_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-delete", "name": "Account Delete"},
    )
    assert create.status_code == 200

    deleted = await async_client.delete("/api/admin/codex-profiles/account-delete")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}

    listed = await async_client.get("/api/admin/codex-profiles")
    assert listed.status_code == 200
    assert [profile["id"] for profile in listed.json()["profiles"] if not profile["is_builtin"]] == []


@pytest.mark.anyio
async def test_delete_active_codex_profile_clears_active_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-active", "name": "Account Active"},
    )
    assert create.status_code == 200

    imported = await async_client.put(
        "/api/admin/codex-profiles/account-active/credentials",
        json={"files": {"auth.json": "{\"token\":\"secret\"}"}},
    )
    assert imported.status_code == 200

    activate = await async_client.post("/api/admin/codex-profiles/account-active/activate")
    assert activate.status_code == 200

    from app.codex_profiles.service import active_codex_profile_env

    assert active_codex_profile_env() != {}

    deleted = await async_client.delete("/api/admin/codex-profiles/account-active")
    assert deleted.status_code == 200
    assert active_codex_profile_env() == {}


@pytest.mark.anyio
async def test_activate_codex_profile_rejects_missing_credentials(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-empty", "name": "Account Empty"},
    )
    assert create.status_code == 200

    activate = await async_client.post("/api/admin/codex-profiles/account-empty/activate")
    assert activate.status_code == 409
    assert activate.json()["detail"] == "codex_profile_credentials_missing"


@pytest.mark.anyio
async def test_deactivate_codex_profile_clears_active_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-a", "name": "Account A"},
    )
    assert create.status_code == 200

    imported = await async_client.put(
        "/api/admin/codex-profiles/account-a/credentials",
        json={"files": {"auth.json": "{\"token\":\"secret\"}"}},
    )
    assert imported.status_code == 200

    activate = await async_client.post("/api/admin/codex-profiles/account-a/activate")
    assert activate.status_code == 200

    from app.codex_profiles.service import active_codex_profile_env, deactivate_codex_profile

    assert active_codex_profile_env() != {}

    deactivate = await async_client.post("/api/admin/codex-profiles/deactivate")
    assert deactivate.status_code == 200
    assert deactivate.json() == {"ok": True}
    assert active_codex_profile_env() == {}


@pytest.mark.anyio
async def test_codex_profile_credential_import_rejects_path_traversal(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-admin-{uuid4()}@example.com")

    create = await async_client.post(
        "/api/admin/codex-profiles",
        json={"id": "account-b", "name": "Account B"},
    )
    assert create.status_code == 200

    imported = await async_client.put(
        "/api/admin/codex-profiles/account-b/credentials",
        json={"files": {"../auth.json": "{\"token\":\"secret\"}"}},
    )
    assert imported.status_code == 400
    assert imported.json()["detail"] == "codex_profile_file_not_allowed"


@pytest.mark.anyio
async def test_cancelled_codex_device_auth_session_stays_cancelled(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()

    from app.codex_profiles import service as codex_profile_service

    codex_profile_service.create_codex_profile("account-c", "Account C")

    class FakeProcess:
        returncode = None
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()

        async def wait(self):
            await asyncio.sleep(0.01)
            self.returncode = 1
            return self.returncode

        def terminate(self):
            self.stdout.feed_eof()
            self.stderr.feed_eof()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(codex_profile_service.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    started = await codex_profile_service.start_device_auth_for_profile("account-c")
    cancelled = await codex_profile_service.cancel_device_auth_session("account-c", started.id)
    assert cancelled.status == "cancelled"

    await asyncio.sleep(0.05)

    polled = codex_profile_service.get_device_auth_session("account-c", started.id)
    assert polled.status == "cancelled"


@pytest.mark.anyio
async def test_device_auth_creates_profile_and_streams_output_while_running(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()

    from app.codex_profiles import service as codex_profile_service

    output_emitted = asyncio.Event()

    class FakeStream:
        def __init__(self):
            self.reads = 0

        async def read(self, _size):
            self.reads += 1
            if self.reads == 1:
                output_emitted.set()
                return b"Visit https://auth.openai.com/codex/device and enter ABCD-EFGH\n"
            await asyncio.Event().wait()

    class FakeProcess:
        returncode = None
        stdout = FakeStream()
        stderr = None
        stopped = asyncio.Event()

        async def wait(self):
            await self.stopped.wait()
            self.returncode = 1
            return self.returncode

        def terminate(self):
            self.stopped.set()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(codex_profile_service.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    started = await codex_profile_service.start_device_auth()
    await asyncio.wait_for(output_emitted.wait(), timeout=1)
    polled = codex_profile_service.get_device_auth_session(started.profile_id, started.id)

    assert started.profile_id.startswith("codex-")
    assert len(started.profile_id) == len("codex-") + 12
    assert polled.status == "running"
    assert "https://auth.openai.com/codex/device" in polled.output
    assert "ABCD-EFGH" in polled.output
    assert (tmp_path / "profiles" / "profiles" / started.profile_id / "profile.json").is_file()
    await codex_profile_service.cancel_device_auth_session(started.profile_id, started.id)


@pytest.mark.anyio
async def test_device_auth_timeout_terminates_waits_and_never_activates(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()

    from app.codex_profiles import service as codex_profile_service

    waited = asyncio.Event()

    class FakeProcess:
        returncode = None
        stdout = None
        stderr = None
        terminated = False

        async def wait(self):
            if not self.terminated:
                await asyncio.Event().wait()
            self.returncode = 1
            waited.set()
            return self.returncode

        def terminate(self):
            self.terminated = True

    process = FakeProcess()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return process

    monkeypatch.setattr(codex_profile_service.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(codex_profile_service, "DEVICE_AUTH_TIMEOUT_SECONDS", 0.01)

    started = await codex_profile_service.start_device_auth()
    await asyncio.wait_for(waited.wait(), timeout=1)
    polled = codex_profile_service.get_device_auth_session(started.profile_id, started.id)

    assert process.terminated is True
    assert polled.status == "timed_out"
    assert polled.error == "codex_device_auth_timed_out"
    assert codex_profile_service.active_codex_profile_env() == {}


@pytest.mark.anyio
async def test_admin_can_start_device_auth_without_profile(async_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_PROFILES_DIR", str(tmp_path / "profiles"))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-device-{uuid4()}@example.com")

    from app.codex_profiles import service as codex_profile_service

    class FakeProcess:
        returncode = None
        stdout = None
        stderr = None
        stopped = asyncio.Event()

        async def wait(self):
            await self.stopped.wait()
            self.returncode = 1
            return self.returncode

        def terminate(self):
            self.stopped.set()

    async def fake_create_subprocess_exec(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(codex_profile_service.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    response = await async_client.post("/api/admin/codex-profiles/device-auth")

    assert response.status_code == 200
    assert response.json()["profile_id"].startswith("codex-")
    await codex_profile_service.cancel_device_auth_session(response.json()["profile_id"], response.json()["id"])


@pytest.mark.anyio
async def test_admin_reads_sanitized_codex_update_status(async_client, tmp_path, monkeypatch):
    status_path = tmp_path / "codex-update-status.json"
    status_path.write_text(
        '{"checked_at":"2026-07-12T01:02:03+00:00","version_before":"codex-cli 1.2.3",'
        '"version_after":"codex-cli 1.2.3","succeeded":false,"error":"update_failed_exit_7"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_UPDATE_STATUS_PATH", str(status_path))
    get_settings.cache_clear()
    await login_admin(async_client, f"codex-status-{uuid4()}@example.com")

    response = await async_client.get("/api/admin/codex-profiles/update-status")

    assert response.status_code == 200
    assert response.json() == {
        "available": True,
        "checked_at": "2026-07-12T01:02:03+00:00",
        "version_before": "codex-cli 1.2.3",
        "version_after": "codex-cli 1.2.3",
        "succeeded": False,
        "error": "update_failed_exit_7",
    }
