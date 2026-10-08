"""Explicit local image capability test; never imported by the production API."""
from __future__ import annotations
import argparse
import asyncio
import json
import shutil
import subprocess
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from app.codex_runtime.artifacts import ArtifactError, stage_image
from app.codex_runtime.launch import DESIGN_MODEL, local_command, local_environment
from app.codex_runtime.rpc import CodexRpc, RpcError


async def run_image_turn(rpc, thread_id, prompt, output, *, reference=None):
    inputs = [{"type": "text", "text": prompt, "text_elements": []}]
    if reference:
        inputs.append({"type": "localImage", "path": str(reference.resolve())})
    response = await rpc.request("turn/start", {
        "threadId": thread_id, "model": DESIGN_MODEL, "effort": "low", "input": inputs,
    })
    turn_id = response["turn"]["id"]
    report = {"turn_id": turn_id, "images": [], "items": [], "messages": []}
    async with asyncio.timeout(480):
        while True:
            event = await rpc.next_event()
            params = event.get("params", {})
            if params.get("threadId") != thread_id:
                continue
            if params.get("turnId", params.get("turn", {}).get("id")) != turn_id:
                continue
            if event["method"] == "item/completed":
                item = params.get("item", {})
                report["items"].append({"type": item.get("type"), "status": item.get("status"),
                                        "result_bytes": len(item.get("result", ""))})
                if item.get("type") == "agentMessage":
                    report["messages"].append(item.get("text", ""))
                if item.get("type") == "imageGeneration":
                    try:
                        image = stage_image(item, runtime_id="local-image-probe", thread_id=thread_id,
                                            turn_id=turn_id, output_root=output, staging_root=output / "staging")
                        record = asdict(image)
                        record["path"] = str(image.path.resolve())
                        report["images"].append(record)
                    except ArtifactError as exc:
                        report["image_error"] = str(exc)
                (output / "progress.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            if event["method"] == "turn/completed":
                report["status"] = params["turn"]["status"]
                return report


async def probe(output, codex_path, edit):
    output.mkdir(parents=True, exist_ok=True)
    report = {"tested_at": datetime.now(UTC).isoformat(), "model": DESIGN_MODEL,
              "mode": "local-single-user-probe", "checks": {},
              "codex_version": subprocess.check_output([codex_path, "--version"], text=True).strip()}
    try:
        async with CodexRpc(local_command(codex_path), cwd=output, timeout=45, env=local_environment()) as rpc:
            response = await rpc.request("thread/start", {
                "model": DESIGN_MODEL, "cwd": str(output.resolve()), "ephemeral": False,
                "approvalPolicy": "never", "sandbox": "workspace-write",
                "developerInstructions": "You are testing KigCraft image design. Use native image generation for image requests. Do not use shell, web or external connectors. Generate only one image per turn.",
            })
            if response.get("model") != DESIGN_MODEL:
                raise RpcError("model_mismatch")
            thread_id = response["thread"]["id"]
            report["thread_id"] = thread_id
            first = await run_image_turn(rpc, thread_id,
                "Create one front-view studio product photo of a fictional kigurumi costume head shell on a plain warm white background. Short coral wig, painted brown eyes, small neutral mouth, no text. Use the native image generation tool.", output)
            report["generate"] = first
            report["checks"]["native_image"] = "pass" if len(first["images"]) == 1 else "blocked"
            if edit and len(first["images"]) == 1:
                second = await run_image_turn(rpc, thread_id,
                    "Edit this actual base image: change only the mouth to a small gentle smile. Keep the wig, eyes, framing, background and rest unchanged. Generate one edited image.", output,
                    reference=Path(first["images"][0]["path"]))
                report["edit"] = second
                report["checks"]["image_input_and_edit"] = "pass" if len(second["images"]) == 1 else "blocked"
    except (RpcError, TimeoutError, OSError) as exc:
        report["checks"]["probe"] = "blocked"
        report["error"] = str(exc) if isinstance(exc, RpcError) else type(exc).__name__
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--edit", action="store_true", help="Also make a second real image edit")
    args = parser.parse_args()
    if not args.codex:
        parser.error("Codex executable not found")
    report = asyncio.run(probe(args.output.resolve(), args.codex, args.edit))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
