import importlib.util
import json
from pathlib import Path

from httpx import ASGITransport, AsyncClient


def load_bridge_module():
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "tools" / "codex_bridge.py"
    spec = importlib.util.spec_from_file_location("codex_bridge_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def make_config(module, tmp_path):
    return module.BridgeConfig(
        root_dir=tmp_path,
        codex_path="codex",
        bridge_token="test-token",
        codex_workspace_dir="runtime/codex",
        codex_output_dir="runtime/generated",
        reference_upload_dir="runtime/references",
        codex_product_reference_path="ref/product.webp",
        generated_public_prefix="/api/generated",
    )


async def test_codex_bridge_health(tmp_path):
    module = load_bridge_module()
    app = module.create_app(config=make_config(module, tmp_path))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://bridge"
    ) as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_codex_bridge_rejects_missing_token(tmp_path):
    module = load_bridge_module()
    app = module.create_app(config=make_config(module, tmp_path))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://bridge"
    ) as client:
        response = await client.post(
            "/generate",
            json={
                "job_id": "job-1",
                "character_session_id": "session-a",
                "reference_keys": ["front:references/upload-1/front.webp"],
                "prompt_text": "prompt",
                "generated_public_prefix": "/api/generated",
            },
        )

    assert response.status_code == 401


async def test_codex_bridge_rejects_reference_traversal(tmp_path):
    module = load_bridge_module()
    app = module.create_app(config=make_config(module, tmp_path))

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://bridge"
    ) as client:
        response = await client.post(
            "/generate",
            headers={"X-Codex-Bridge-Token": "test-token"},
            json={
                "job_id": "job-1",
                "character_session_id": "session-a",
                "reference_keys": ["front:references/../product.webp"],
                "prompt_text": "prompt",
                "generated_public_prefix": "/api/generated",
            },
        )

    assert response.status_code == 400
    assert "reference" in response.text.lower()


async def test_codex_bridge_runs_runner_and_returns_four_public_outputs(tmp_path):
    module = load_bridge_module()
    config = make_config(module, tmp_path)
    product_file = tmp_path / "ref" / "product.webp"
    product_file.parent.mkdir(parents=True)
    product_file.write_bytes(b"product")
    reference_file = tmp_path / "runtime" / "references" / "upload-1" / "front.webp"
    reference_file.parent.mkdir(parents=True)
    reference_file.write_bytes(b"front")
    runner_calls = []

    async def fake_runner(command, workspace):
        runner_calls.append((command, workspace))
        outputs_dir = workspace / "outputs"
        outputs_dir.mkdir(parents=True)
        outputs = []
        for index in range(1, 5):
            output_path = outputs_dir / f"candidate-{index}.webp"
            output_path.write_bytes(f"candidate-{index}".encode("ascii"))
            outputs.append(
                {
                    "index": index,
                    "path": f"outputs/candidate-{index}.webp",
                    "width": 2048,
                    "height": 1536,
                }
            )
        (workspace / "manifest.json").write_text(
            json.dumps({"generation_source": "image_generation_tool", "outputs": outputs}),
            encoding="utf-8",
        )
        return module.CommandResult(returncode=0, stdout=b'{"type":"done"}\n', stderr=b"")

    app = module.create_app(config=config, runner=fake_runner)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://bridge"
    ) as client:
        response = await client.post(
            "/generate",
            headers={"X-Codex-Bridge-Token": "test-token"},
            json={
                "job_id": "job-1",
                "character_session_id": "session-a",
                "reference_keys": ["front:references/upload-1/front.webp"],
                "prompt_payload": {"user_notes": "keep hair tips"},
                "prompt_text": "compose four finished views",
                "generated_public_prefix": "/api/generated",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [output["index"] for output in body["outputs"]] == [1, 2, 3, 4]
    assert body["outputs"][0]["image_url"] == (
        "/api/generated/session-a/job-1/outputs/candidate-1.webp"
    )
    assert (
        tmp_path
        / "runtime"
        / "generated"
        / "session-a"
        / "job-1"
        / "outputs"
        / "candidate-1.webp"
    ).read_bytes() == b"candidate-1"

    assert len(runner_calls) == 1
    command, workspace = runner_calls[0]
    image_args = [
        Path(command[index + 1])
        for index, item in enumerate(command)
        if item == "--image"
    ]
    assert image_args == [product_file, reference_file]
    assert (workspace / "prompt.md").read_text(encoding="utf-8") == (
        "compose four finished views"
    )
    assert (workspace / "codex-events.jsonl").read_bytes() == b'{"type":"done"}\n'


async def test_codex_bridge_generates_one_candidate_output(tmp_path):
    module = load_bridge_module()
    config = make_config(module, tmp_path)
    product_file = tmp_path / "ref" / "product.webp"
    product_file.parent.mkdir(parents=True)
    product_file.write_bytes(b"product")
    reference_file = tmp_path / "runtime" / "references" / "upload-1" / "front.webp"
    reference_file.parent.mkdir(parents=True)
    reference_file.write_bytes(b"front")

    async def fake_runner(command, workspace):
        outputs_dir = workspace / "outputs"
        outputs_dir.mkdir(parents=True)
        output_path = outputs_dir / "candidate-2.webp"
        output_path.write_bytes(b"candidate-2")
        (workspace / "manifest.json").write_text(
            json.dumps(
                {
                    "generation_source": "image_generation_tool",
                    "outputs": [
                        {
                            "index": 2,
                            "path": "outputs/candidate-2.webp",
                            "width": 2048,
                            "height": 1536,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return module.CommandResult(returncode=0, stdout=b'{"type":"done"}\n', stderr=b"")

    app = module.create_app(config=config, runner=fake_runner)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://bridge"
    ) as client:
        response = await client.post(
            "/generate-candidate",
            headers={"X-Codex-Bridge-Token": "test-token"},
            json={
                "job_id": "job-1",
                "character_session_id": "session-a",
                "output_index": 2,
                "reference_keys": ["front:references/upload-1/front.webp"],
                "prompt_payload": {"user_notes": "keep hair tips"},
                "prompt_text": "compose side finished view",
                "generated_public_prefix": "/api/generated",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert [output["index"] for output in body["outputs"]] == [2]
    assert body["outputs"][0]["image_url"] == (
        "/api/generated/session-a/job-1/outputs/candidate-2.webp"
    )
    assert (
        tmp_path
        / "runtime"
        / "generated"
        / "session-a"
        / "job-1"
        / "outputs"
        / "candidate-2.webp"
    ).read_bytes() == b"candidate-2"

