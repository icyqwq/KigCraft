"""Write a reviewed per-account sandbox Compose manifest. Never runs Docker or copies credentials."""
import argparse
import json
from pathlib import Path
from app.core.config import Settings
from app.codex_runtime.selection import container_spec


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise ValueError()
        settings = Settings(_env_file=args.env_file)
        if settings.v2_runtime_mode != "container" or not settings.v2_enabled:
            raise ValueError()
        manifest = container_spec(settings, args.owner_id).compose()
        with args.output.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
    except Exception:
        raise SystemExit("container_manifest_unavailable") from None
    print(json.dumps({"status": "manifest_written", "owner_id": args.owner_id,
                      "services_started": False, "credentials_copied": False}))


if __name__ == "__main__":
    main()
