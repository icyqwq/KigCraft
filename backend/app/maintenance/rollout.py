"""Inspect or atomically replace the V2 sticky rollout policy; never start tasks."""
import argparse
import asyncio
import json
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.admin.rollout import Policy, configure, inspect
from app.core.config import Settings
from app.db.v2_schema import require_v2_schema
from app.design.service import ApplicationError


async def run(settings, args):
    if not settings.v2_enabled:
        raise ApplicationError("v2_disabled")
    policy = None
    if args.command == "apply":
        if not args.policy_file or args.policy_file.stat().st_size > 1024 * 1024:
            raise ApplicationError("invalid_rollout_policy")
        policy = Policy.model_validate_json(args.policy_file.read_text(encoding="utf-8"))
    engine = create_async_engine(settings.database_url, hide_parameters=True)
    try:
        await require_v2_schema(settings=settings, engine=engine)
        async with async_sessionmaker(engine, expire_on_commit=False).begin() as db:
            if args.command == "inspect":
                return await inspect(db)
            return await configure(db, args.actor_id, args.request_id, args.reason,
                                   args.expected_digest, policy)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "apply"])
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--actor-id")
    parser.add_argument("--request-id")
    parser.add_argument("--reason")
    parser.add_argument("--expected-digest")
    args = parser.parse_args()
    try:
        if not args.env_file.is_file():
            raise ApplicationError("configuration_file_missing")
        if args.command == "apply" and not all((args.policy_file, args.actor_id, args.request_id,
                                                args.reason, args.expected_digest)):
            raise ApplicationError("rollout_arguments_required")
        result = asyncio.run(run(Settings(_env_file=args.env_file), args))
    except Exception as exc:
        print(json.dumps({"status": "refused", "code": exc.code if isinstance(exc, ApplicationError)
                          else "rollout_configuration_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
