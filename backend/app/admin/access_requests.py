"""HTTP and stream rejection observation; response bodies and headers stay unchanged."""
import asyncio
from starlette.background import BackgroundTask, BackgroundTasks

from app.core.config import get_settings
from app.admin.access_references import request_references


def supported(request):
    route = getattr(request.scope.get("route"), "path", "").removeprefix("/api")
    generated = getattr(request.scope.get("endpoint"), "__name__", "") == "generated_asset"
    return get_settings().v2_enabled and (
        route.startswith(("/v2/", "/generation/jobs", "/references/", "/album/items")) or route == "/references" or generated)


async def _capture(request, *, stream=False, status=400):
    if not supported(request):
        return
    from app.admin.access_observations import record_rejection
    route = getattr(request.scope.get("route"), "path", "")
    actor = getattr(request.state, "v2_actor_id", None)
    auth_failure = getattr(request.state, "access_auth_failure", None)
    outcome = auth_failure or ("unauthenticated" if status == 401 else "forbidden" if status == 403 else "rejected")
    params = dict(request.path_params)
    if getattr(request.scope.get("endpoint"), "__name__", "") == "generated_asset":
        params["_generated_asset"] = True
    # Never read an unauthenticated body or perform an ownership lookup on its behalf.
    refs, complete = await request_references(request, route) if actor and not auth_failure else ([], True)
    await record_rejection(actor, route, params, references=refs, complete=complete,
        collector=getattr(request.app.state, "access_collector", None),
        outcome=outcome, stream=stream, authenticated=not bool(auth_failure))


async def capture(request, *, stream=False, status=400):
    collector = getattr(request.app.state, "access_collector", None)
    try:
        await asyncio.wait_for(_capture(request, stream=stream, status=status), timeout=3)
    except asyncio.CancelledError:
        if collector is not None:
            collector.failed()
        raise
    except Exception:
        if collector is not None:
            collector.failed()
            await collector.pulse()
    # No private exception details enter the response or logs.


def observed_response(request, response):
    if 400 <= response.status_code < 500 and supported(request):
        task = BackgroundTask(capture, request, status=response.status_code)
        response.background = BackgroundTasks([response.background, task]) if response.background else task
    return response


async def observed_stream(request, source):
    try:
        async for part in source:
            yield part
    finally:
        await source.aclose()
        if getattr(request.state, "access_stream_rejected", False):
            await capture(request, stream=True)
