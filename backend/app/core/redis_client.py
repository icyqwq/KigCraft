import asyncio

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import get_settings

_redis_clients: dict[int, redis.Redis] = {}


def get_redis_client() -> redis.Redis:
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    client = _redis_clients.get(loop_id)
    if client is None:
        client = redis.from_url(get_settings().redis_url, decode_responses=True)
        _redis_clients[loop_id] = client
    return client


async def flush_verification_send_rate_limits() -> None:
    try:
        redis_client = get_redis_client()
    except RuntimeError:
        return

    try:
        async for key in redis_client.scan_iter("auth:send-code:*"):
            await redis_client.delete(key)
    except RedisError:
        return
