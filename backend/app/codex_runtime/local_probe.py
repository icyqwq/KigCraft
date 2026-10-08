"""Opt-in capability probe. Uses the local Codex login; never runs in normal CI.

Run from backend: python -m app.codex_runtime.local_probe --output ../work/v2/probe
This is a single-user development probe, not a multi-tenant runtime.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.codex_runtime.rpc import CodexRpc, RpcError

from app.codex_runtime.launch import DESIGN_MODEL, local_command, local_environment


async def models(rpc: CodexRpc) -> list[dict]:
    found = []
    cursor = None
    seen = set()
    for _ in range(100):
        result = await rpc.request("model/list", {"includeHidden": True, "limit": 100, "cursor": cursor})
        found.extend(result.get("data", []))
        cursor = result.get("nextCursor")
        if cursor is None:
            return found
        if cursor in seen:
            raise RpcError("model_cursor_loop")
        seen.add(cursor)
    raise RpcError("model_page_limit")


async def turn(rpc: CodexRpc, thread_id: str, text: str, *, model: str = DESIGN_MODEL) -> dict:
    response = await rpc.request("turn/start", {
        "threadId": thread_id, "model": model, "effort": "low",
        "input": [{"type": "text", "text": text, "text_elements": []}],
    })
    turn_id = response["turn"]["id"]
    items = {}
    methods: set[str] = set()
    async with asyncio.timeout(180):
        while True:
            event = await rpc.next_event()
            methods.add(event["method"])
            params = event.get("params", {})
            if params.get("threadId") != thread_id:
                continue
            if params.get("turnId", params.get("turn", {}).get("id")) != turn_id:
                continue
            if event["method"] == "item/completed":
                item = params.get("item", {})
                if item.get("type") == "agentMessage":
                    items[item["id"]] = item["text"]
            if event["method"] == "turn/completed":
                return {"turn_id": turn_id, "status": params["turn"]["status"],
                        "text": "\n".join(items.values()), "event_methods": sorted(methods)}


async def probe(output: Path, codex_path: str, *, conversation: bool) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    report = {
        "tested_at": datetime.now(UTC).isoformat(),
        "mode": "local-single-user-probe",
        "requested_model": DESIGN_MODEL,
        "codex_version": subprocess.check_output([codex_path, "--version"], text=True).strip(),
        "checks": {},
    }
    checks = report["checks"]
    command = local_command(codex_path, images=False)
    try:
        async with CodexRpc(command, cwd=output, timeout=45, env=local_environment()) as rpc:
            catalogue = await models(rpc)
            report["models"] = [{"model": m.get("model"), "input_modalities": m.get("inputModalities")} for m in catalogue]
            available = any(m.get("model") == DESIGN_MODEL for m in catalogue)
            checks["design_model_catalogue"] = "pass" if available else "blocked"
            if not available or not conversation:
                return report
            response = await rpc.request("thread/start", {
                "model": DESIGN_MODEL, "cwd": str(output.resolve()), "ephemeral": False,
                "approvalPolicy": "never", "sandbox": "read-only",
                "developerInstructions": "This is a protocol smoke test. Reply only to the test prompt. Do not use tools or inspect files.",
            })
            actual = response.get("model")
            report["actual_model"] = actual
            if actual != DESIGN_MODEL:
                raise RpcError("model_mismatch")
            thread_id = response["thread"]["id"]
            first = await turn(rpc, thread_id, "Remember the test label KIG-V2-CORAL. Reply exactly KIG-V2-CORAL.")
            second = await turn(rpc, thread_id, "What test label did I ask you to remember? Reply with the label only.")
            report["conversation"] = [first, second]
            checks["two_turns"] = "pass" if all(t["status"] == "completed" and "KIG-V2-CORAL" in t["text"] for t in (first, second)) else "fail"
            report["thread_id"] = thread_id
        async with CodexRpc(command, cwd=output, timeout=45, env=local_environment()) as rpc:
            resumed = await rpc.request("thread/resume", {
                "threadId": thread_id, "model": DESIGN_MODEL, "cwd": str(output.resolve()),
                "approvalPolicy": "never", "sandbox": "read-only",
            })
            if resumed.get("model") != DESIGN_MODEL:
                raise RpcError("model_mismatch")
            third = await turn(rpc, thread_id, "Repeat our test label, nothing else.")
            report["resume"] = third
            checks["restart_resume"] = "pass" if third["status"] == "completed" and "KIG-V2-CORAL" in third["text"] else "fail"
    except (RpcError, TimeoutError, OSError) as exc:
        checks["probe"] = "blocked"
        report["error"] = str(exc) if isinstance(exc, RpcError) else type(exc).__name__
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--conversation", action="store_true", help="Make three real Sol calls to verify continuity and restart")
    args = parser.parse_args()
    if not args.codex:
        parser.error("Local Codex executable not found")
    result = asyncio.run(probe(args.output.resolve(), args.codex, conversation=args.conversation))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
