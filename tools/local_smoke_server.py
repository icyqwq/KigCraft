from __future__ import annotations

import json
import mimetypes
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
APP_FILE = ROOT / "tools" / "smoke_app.html"


PROMPT_CHIPS = [
    {"id": "soft-baby-face", "category": "face", "label": "圆润幼态脸", "text": "face proportions softer and younger", "sort_order": 10},
    {"id": "larger-soft-eyes", "category": "eyes", "label": "更大更柔和眼睛", "text": "larger softer eyes", "sort_order": 20},
    {"id": "shy-expression", "category": "expression", "label": "害羞表情", "text": "shy expression", "sort_order": 30},
    {"id": "real-wig-fibers", "category": "material", "label": "真实假发纤维", "text": "realistic wig fibers", "sort_order": 40},
]


def generation_job() -> dict[str, object]:
    job_id = str(uuid.uuid4())
    return {
        "id": job_id,
        "project_id": "demo-project",
        "status": "succeeded",
        "progress": 100,
        "outputs": [
            {"index": index, "object_key": f"mock/{job_id}/{index}.png", "width": 2048, "height": 1536}
            for index in range(1, 5)
        ],
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/prompts/chips":
            self.send_json(PROMPT_CHIPS)
            return
        if path in {"/", "/index.html"}:
            self.serve_file(APP_FILE)
            return
        self.send_error(404)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/generation/projects/demo-project/jobs":
            length = int(self.headers.get("Content-Length", "0"))
            if length:
                self.rfile.read(length)
            self.send_json(generation_job())
            return
        self.send_error(404)

    def serve_file(self, path: Path) -> None:
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 5183), Handler)
    print("smoke server: http://127.0.0.1:5183", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
