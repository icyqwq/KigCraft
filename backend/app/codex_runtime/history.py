"""Read persisted turn evidence without loading, steering, or restarting a session."""
from __future__ import annotations
from app.codex_runtime.launch import DESIGN_MODEL
from app.codex_runtime.rpc import RpcError
from app.codex_runtime.session import TurnResult


async def read_turn(rpc, *, thread_id: str, turn_id: str | None, client_request_id: str) -> TurnResult:
    response = await rpc.request("thread/read", {"threadId": thread_id, "includeTurns": True})
    thread = response.get("thread", {})
    if thread.get("id") != thread_id or thread.get("model") != DESIGN_MODEL:
        raise RpcError("history_binding_mismatch")
    turns = thread.get("turns")
    if not isinstance(turns, list):
        raise RpcError("history_incomplete")
    matches = []
    for turn in turns:
        items = turn.get("items", [])
        client_ids = [i.get("clientId") for i in items if i.get("type") == "userMessage"]
        if (turn_id and turn.get("id") == turn_id) or client_request_id in client_ids:
            if turn_id and turn.get("id") != turn_id:
                raise RpcError("history_identity_conflict")
            if client_ids != [client_request_id]:
                raise RpcError("history_identity_conflict")
            matches.append(turn)
    if len(matches) != 1:
        # Absence does not prove that turn/start was never accepted.
        raise RpcError("history_turn_unconfirmed")
    turn = matches[0]
    if turn.get("itemsView", "full") != "full":
        raise RpcError("history_incomplete")
    if not isinstance(turn.get("id"), str) or not turn["id"]:
        raise RpcError("history_identity_conflict")
    if turn.get("status") not in {"completed", "failed", "interrupted"}:
        raise RpcError("history_turn_unconfirmed")
    messages, images, seen = [], [], set()
    for item in turn["items"]:
        if item.get("type") not in {"agentMessage", "imageGeneration"}:
            continue
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or identity in seen:
            raise RpcError("history_item_identity_conflict")
        seen.add(identity)
        if item["type"] == "agentMessage":
            if not isinstance(item.get("text"), str):
                raise RpcError("invalid_agent_message")
            messages.append({"id": identity, "text": item["text"], "phase": item.get("phase")})
        else:
            images.append(item)
    return TurnResult(turn["id"], turn["status"], messages, images)
