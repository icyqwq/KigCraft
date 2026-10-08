"""Opt-in real probes for fork boundaries, separate review and no-image mode."""
import argparse
import asyncio
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from app.codex_runtime.launch import local_command, local_environment
from app.codex_runtime.rpc import CodexRpc, RpcError
from app.codex_runtime.session import CodexSession, text_input
from app.moderation.reviewer import review, ModerationUnavailable


async def probe(output, codex, review_model):
    output.mkdir(parents=True, exist_ok=True)
    report = {"tested_at": datetime.now(UTC).isoformat(), "checks": {}}
    command = local_command(codex, images=False)
    try:
        async with CodexRpc(command, cwd=output, env=local_environment(), timeout=45) as rpc:
            session = await CodexSession.open(rpc, instructions="This is a protocol test. Respond literally and do not use tools.")
            first = await session.run([text_input("Remember the current test label CORAL. Reply CORAL.")])
            second = await session.run([text_input("Replace our current test label with INDIGO. Reply INDIGO.")])
            fork = await CodexSession.open(rpc, thread_id=session.binding.thread_id, fork_after=first.turn_id,
                                          instructions="This is a protocol test. Respond literally and do not use tools.")
            result = await fork.run([text_input("What is our current test label? Reply with that label only.")])
            text = "\n".join(m["text"] for m in result.messages)
            report["fork"] = {"status": result.status, "text": text, "model": fork.binding.model}
            report["checks"]["exact_fork"] = "pass" if result.status == "completed" and "CORAL" in text and "INDIGO" not in text else "fail"
        async with CodexRpc(command, cwd=output, env=local_environment(), timeout=45) as rpc:
            decision = await review(rpc, review_model=review_model,
                                    user_text="Design a fictional coral wig with brown painted eyes.",
                                    references=[{"usage": "This color palette has no face; use colors only."}],
                                    image_inputs=[])
            report["review"] = {"model": review_model, **decision.model_dump()}
            report["checks"]["independent_review"] = "pass" if decision.verdict == "allow" else "fail"
        async with CodexRpc(command, cwd=output, env=local_environment(), timeout=45) as rpc:
            session = await CodexSession.open(rpc)
            result = await session.run([text_input("Generate an image of a simple red circle now. Use any image generation tools available, even though this is a text-only test.")])
            report["discussion"] = {"status": result.status, "image_count": len(result.images),
                                    "text": "\n".join(m["text"] for m in result.messages)}
            report["checks"]["no_image_sample"] = "sample_pass" if result.status == "completed" and not result.images else "fail"
            report["checks"]["discussion_hard_boundary"] = "pending_tool_surface_verification"
    except (RpcError, TimeoutError, ModerationUnavailable, OSError) as exc:
        report["checks"]["probe"] = "blocked"
        report["error"] = str(exc) if isinstance(exc, (RpcError, ModerationUnavailable)) else type(exc).__name__
    finally:
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--codex", default=shutil.which("codex"))
    parser.add_argument("--review-model", default="gpt-6-luna")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.output.resolve(), args.codex, args.review_model)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
