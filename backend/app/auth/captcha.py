from __future__ import annotations

import httpx
from fastapi import HTTPException

from app.core.config import get_settings


async def verify_captcha_token(token: str, *, remote_ip: str | None = None) -> None:
    settings = get_settings()
    if not settings.captcha_enabled:
        return
    if not token.strip():
        raise HTTPException(status_code=400, detail="captcha_required")
    ok = await _verify_turnstile_siteverify(token, remote_ip=remote_ip)
    if not ok:
        raise HTTPException(status_code=400, detail="captcha_invalid")


async def _verify_turnstile_siteverify(token: str, *, remote_ip: str | None = None) -> bool:
    settings = get_settings()
    payload = {
        "secret": settings.turnstile_secret_key,
        "response": token,
    }
    if remote_ip:
        payload["remoteip"] = remote_ip
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(settings.turnstile_timeout_seconds)) as client:
            response = await client.post(settings.turnstile_verify_url, data=payload)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return False
    return bool(data.get("success"))
