import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.generation.codex_manifest import parse_codex_manifest  # noqa: E402
from app.generation.codex_usage import parse_codex_usage_payload, usage_payload_from_env  # noqa: E402
from app.generation.usage import extract_token_usage_from_codex_events  # noqa: E402


@dataclass(frozen=True)
class BridgeConfig:
    root_dir: Path
    codex_path: str
    bridge_token: str
    codex_workspace_dir: str = "runtime/codex"
    codex_output_dir: str = "runtime/generated"
    reference_upload_dir: str = "runtime/references"
    codex_product_reference_path: str = "ref/product-reference.png"
    generated_public_prefix: str = "/api/generated"

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        root_dir = Path(os.getenv("KIG_PREVIEW_ROOT") or REPO_ROOT).resolve()
        return cls(
            root_dir=root_dir,
            codex_path=os.getenv("CODEX_PATH") or _default_codex_path(root_dir),
            bridge_token=os.getenv("CODEX_BRIDGE_TOKEN") or "change-me-local-bridge-token",
            codex_workspace_dir=os.getenv("CODEX_WORKSPACE_DIR", "runtime/codex"),
            codex_output_dir=os.getenv("CODEX_OUTPUT_DIR", "runtime/generated"),
            reference_upload_dir=os.getenv("REFERENCE_UPLOAD_DIR", "runtime/references"),
            codex_product_reference_path=os.getenv(
                "CODEX_PRODUCT_REFERENCE_PATH", "ref/product-reference.png"
            ),
            generated_public_prefix=os.getenv("GENERATED_PUBLIC_PREFIX", "/api/generated"),
        )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes


class GenerateRequest(BaseModel):
    job_id: str
    character_session_id: str
    reference_keys: list[str] = Field(default_factory=list)
    prompt_payload: dict[str, Any] = Field(default_factory=dict)
    prompt_text: str
    generated_public_prefix: str = "/api/generated"


class GenerateCandidateRequest(GenerateRequest):
    output_index: int = Field(ge=1, le=4)


class CancelRequest(BaseModel):
    job_id: str
    character_session_id: str


Runner = Callable[[list[str], Path], Awaitable[CommandResult]]
ACTIVE_PROCESSES: dict[tuple[str, str], asyncio.subprocess.Process] = {}


def create_app(
    *, config: BridgeConfig | None = None, runner: Runner | None = None
) -> FastAPI:
    bridge_config = config or BridgeConfig.from_env()
    command_runner = runner or run_codex_command
    app = FastAPI(title="Kig Preview Codex Bridge")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/usage")
    async def usage(x_codex_bridge_token: str | None = Header(default=None)) -> dict[str, Any]:
        if x_codex_bridge_token != bridge_config.bridge_token:
            raise HTTPException(status_code=401, detail="Invalid Codex bridge token")
        status = usage_payload_from_env(os.getenv("CODEX_USAGE_STATUS_JSON"))
        if status is None:
            status = await _usage_from_command(os.getenv("CODEX_USAGE_COMMAND"))
        if status is None:
            raise HTTPException(status_code=404, detail="Codex usage status is unavailable")
        return {
            "remaining_percent": status.remaining_percent,
            "reset_after_seconds": status.wait_seconds,
            "reset_at": status.reset_at.isoformat() if status.reset_at else None,
        }

    @app.post("/generate")
    async def generate(
        request: GenerateRequest,
        x_codex_bridge_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_codex_bridge_token != bridge_config.bridge_token:
            raise HTTPException(status_code=401, detail="Invalid Codex bridge token")
        return await generate_with_codex_bridge(request, bridge_config, command_runner)

    @app.post("/generate-candidate")
    async def generate_candidate(
        request: GenerateCandidateRequest,
        x_codex_bridge_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_codex_bridge_token != bridge_config.bridge_token:
            raise HTTPException(status_code=401, detail="Invalid Codex bridge token")
        return await generate_candidate_with_codex_bridge(
            request, bridge_config, command_runner
        )

    @app.post("/cancel")
    async def cancel(
        request: CancelRequest,
        x_codex_bridge_token: str | None = Header(default=None),
    ) -> dict[str, bool]:
        if x_codex_bridge_token != bridge_config.bridge_token:
            raise HTTPException(status_code=401, detail="Invalid Codex bridge token")
        cancelled = await cancel_codex_run(
            _safe_path_segment(request.character_session_id),
            _safe_path_segment(request.job_id),
        )
        return {"cancelled": cancelled}

    return app


async def generate_with_codex_bridge(
    request: GenerateRequest, config: BridgeConfig, runner: Runner
) -> dict[str, Any]:
    session_id = _safe_path_segment(request.character_session_id)
    job_id = _safe_path_segment(request.job_id)
    workspace = _resolve_config_path(config.root_dir, config.codex_workspace_dir) / session_id / job_id
    public_output_dir = _resolve_config_path(config.root_dir, config.codex_output_dir) / session_id / job_id
    workspace.mkdir(parents=True, exist_ok=True)

    (workspace / "prompt.md").write_text(request.prompt_text, encoding="utf-8")
    (workspace / "prompt_payload.json").write_text(
        json.dumps(request.prompt_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    image_paths = _resolve_image_paths(request.reference_keys, config)
    command = _build_codex_command(config, workspace, image_paths, request.prompt_text)
    result = await runner(command, workspace)
    (workspace / "codex-events.jsonl").write_bytes(result.stdout or b"")
    (workspace / "codex-stderr.log").write_bytes(result.stderr or b"")
    token_usage = extract_token_usage_from_codex_events(result.stdout or b"")

    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Codex CLI failed with exit code {result.returncode}; "
                f"see {workspace / 'codex-stderr.log'}"
            ),
        )

    outputs = parse_codex_manifest(
        workspace / "manifest.json",
        public_prefix=f"{config.generated_public_prefix.rstrip('/')}/{session_id}/{job_id}",
        generation_mode=str(request.prompt_payload.get("generation_mode") or "front_design"),
    )
    _copy_public_outputs(outputs, workspace, public_output_dir)
    return {
        "outputs": [output.__dict__ for output in outputs],
        "token_usage": token_usage.to_dict() if token_usage else None,
    }


async def generate_candidate_with_codex_bridge(
    request: GenerateCandidateRequest, config: BridgeConfig, runner: Runner
) -> dict[str, Any]:
    session_id = _safe_path_segment(request.character_session_id)
    job_id = _safe_path_segment(request.job_id)
    workspace = _resolve_config_path(config.root_dir, config.codex_workspace_dir) / session_id / job_id
    public_output_dir = _resolve_config_path(config.root_dir, config.codex_output_dir) / session_id / job_id
    workspace.mkdir(parents=True, exist_ok=True)

    (workspace / "prompt.md").write_text(request.prompt_text, encoding="utf-8")
    (workspace / f"prompt-candidate-{request.output_index}.md").write_text(
        request.prompt_text, encoding="utf-8"
    )
    (workspace / "prompt_payload.json").write_text(
        json.dumps(request.prompt_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    image_paths = _resolve_image_paths(request.reference_keys, config)
    command = _build_codex_command(config, workspace, image_paths, request.prompt_text)
    result = await runner(command, workspace)
    (workspace / f"codex-events-candidate-{request.output_index}.jsonl").write_bytes(
        result.stdout or b""
    )
    (workspace / f"codex-stderr-candidate-{request.output_index}.log").write_bytes(
        result.stderr or b""
    )
    (workspace / "codex-events.jsonl").write_bytes(result.stdout or b"")
    (workspace / "codex-stderr.log").write_bytes(result.stderr or b"")
    token_usage = extract_token_usage_from_codex_events(result.stdout or b"")

    if result.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Codex CLI failed with exit code {result.returncode}; "
                f"see {workspace / f'codex-stderr-candidate-{request.output_index}.log'}"
            ),
        )

    outputs = parse_codex_manifest(
        workspace / "manifest.json",
        public_prefix=f"{config.generated_public_prefix.rstrip('/')}/{session_id}/{job_id}",
        expected_indexes=[request.output_index],
        generation_mode=str(request.prompt_payload.get("generation_mode") or "front_design"),
    )
    _copy_public_outputs(outputs, workspace, public_output_dir)
    return {
        "outputs": [output.__dict__ for output in outputs],
        "token_usage": token_usage.to_dict() if token_usage else None,
    }


async def run_codex_command(command: list[str], workspace: Path) -> CommandResult:
    process_key = _process_key_for_workspace(workspace)
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
    except OSError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to start Codex CLI: {exc}") from exc

    ACTIVE_PROCESSES[process_key] = process
    try:
        stdout, stderr = await process.communicate()
        return CommandResult(
            returncode=process.returncode or 0,
            stdout=stdout or b"",
            stderr=stderr or b"",
        )
    finally:
        if ACTIVE_PROCESSES.get(process_key) is process:
            ACTIVE_PROCESSES.pop(process_key, None)


async def _usage_from_command(command: str | None) -> Any:
    if not command or not command.strip():
        return None
    process = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await process.communicate()
    if process.returncode != 0:
        return None
    try:
        payload = json.loads((stdout or b"").decode("utf-8", errors="ignore"))
    except json.JSONDecodeError:
        return None
    return parse_codex_usage_payload(payload)


async def cancel_codex_run(session_id: str, job_id: str) -> bool:
    process = ACTIVE_PROCESSES.get((session_id, job_id))
    if process is None or process.returncode is not None:
        return False

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
    return True


def _build_codex_command(
    config: BridgeConfig, workspace: Path, image_paths: list[Path], prompt_text: str
) -> list[str]:
    command = [
        _resolve_codex_path(config.codex_path, config.root_dir),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-C",
        str(workspace),
        "-o",
        str(workspace / "last-message.txt"),
    ]
    for image_path in image_paths:
        command.extend(["--image", str(image_path)])
    command.append("--")
    command.append(prompt_text)
    return command


def _resolve_image_paths(reference_keys: list[str], config: BridgeConfig) -> list[Path]:
    reference_root = _resolve_config_path(config.root_dir, config.reference_upload_dir)
    user_references = [
        _resolve_uploaded_reference_path(reference_key, reference_root)
        for reference_key in reference_keys
    ]
    user_references = [reference for reference in user_references if reference is not None]
    if not user_references:
        raise HTTPException(
            status_code=400,
            detail="Codex bridge generation requires at least one uploaded reference",
        )

    product_reference = _resolve_config_path(
        config.root_dir, config.codex_product_reference_path
    )
    if not product_reference.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Fixed product reference is missing: {product_reference}",
        )

    return [product_reference, *user_references]


def _resolve_uploaded_reference_path(reference_key: Any, reference_root: Path) -> Path | None:
    if not isinstance(reference_key, str) or not reference_key:
        raise HTTPException(status_code=400, detail="Invalid reference key")
    if "://" in reference_key or reference_key.startswith("//"):
        raise HTTPException(status_code=400, detail="Invalid reference key")

    normalized = reference_key.replace("\\", "/")
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    if normalized.startswith("//"):
        raise HTTPException(status_code=400, detail="Invalid reference key")

    posix_path = PurePosixPath(normalized)
    if (
        posix_path.is_absolute()
        or any(part == ".." for part in posix_path.parts)
        or len(posix_path.parts) < 3
        or posix_path.parts[0] != "references"
    ):
        raise HTTPException(status_code=400, detail="Invalid reference key")

    candidate = (reference_root / Path(*posix_path.parts[1:])).resolve()
    try:
        candidate.relative_to(reference_root.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid reference key") from exc
    if not candidate.is_file():
        raise HTTPException(status_code=400, detail=f"Reference file is missing: {normalized}")
    return candidate


def _copy_public_outputs(outputs: list[Any], workspace: Path, public_output_dir: Path) -> None:
    public_output_dir.mkdir(parents=True, exist_ok=True)
    for output in outputs:
        relative_path = _relative_output_path(output.image_url)
        source = workspace / relative_path
        destination = public_output_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _relative_output_path(image_url: str) -> Path:
    normalized = image_url.replace("\\", "/")
    marker = "/outputs/"
    marker_index = normalized.find(marker)
    if marker_index < 0:
        raise HTTPException(status_code=500, detail="Generated image URL lacks outputs path")
    return Path(*PurePosixPath(normalized[marker_index + 1 :]).parts)


def _safe_path_segment(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return sanitized or "unknown"


def _resolve_config_path(root_dir: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (root_dir / path).resolve()


def _process_key_for_workspace(workspace: Path) -> tuple[str, str]:
    return (workspace.parent.name, workspace.name)


def _resolve_codex_path(codex_path: str, root_dir: Path) -> str:
    path = Path(codex_path)
    if path.is_absolute():
        return str(path)
    if "/" in codex_path or "\\" in codex_path:
        return str((root_dir / path).resolve())
    candidate = (root_dir / path).resolve()
    if candidate.exists():
        return str(candidate)
    return codex_path


def _default_codex_path(root_dir: Path) -> str:
    candidates = [root_dir / ".tools" / "codex.exe"]
    if root_dir.parent.name == ".worktrees":
        candidates.append(root_dir.parent.parent / ".tools" / "codex.exe")
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(candidates[0])


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("CODEX_BRIDGE_HOST", "127.0.0.1")
    port = int(os.getenv("CODEX_BRIDGE_PORT", "18100"))
    uvicorn.run(app, host=host, port=port)
