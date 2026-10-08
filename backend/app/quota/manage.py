"""Explicit operator CLI. Uses V2_DATABASE_URL only; never migrates all users implicitly."""
import argparse
import asyncio
import json
import os
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.design.service import ApplicationError
from app.quota.adjustments import adjust, refund
from app.quota.cutover import cut_over, freeze

async def run(args):
    url = os.environ.get("V2_DATABASE_URL")
    if not url or not url.startswith("postgresql+asyncpg://"):
        raise ApplicationError("explicit_v2_database_url_required")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory.begin() as db:
            common = {"actor_id": args.actor, "reason": args.reason}
            if args.action == "freeze":
                wallet = await freeze(db, args.owner, **common)
                result = {"owner_id": wallet.owner_id, "write_owner": wallet.write_owner}
            elif args.action == "cutover":
                wallet = await cut_over(db, args.owner, **common)
                result = {"owner_id": wallet.owner_id, "write_owner": wallet.write_owner,
                          "balance": wallet.balance, "reserved": wallet.reserved}
            else:
                record = (await adjust(db, args.owner, delta=args.delta, key=args.key, **common)
                          if args.action == "adjust" else
                          await refund(db, args.operation, units=args.units, key=args.key, **common))
                result = {"entry_id": record.id, "balance": record.balance_after,
                          "reserved": record.reserved_after}
        return result
    finally:
        await engine.dispose()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    for action in ("freeze", "cutover", "adjust", "refund"):
        command = commands.add_parser(action)
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--operation" if action == "refund" else "--owner", required=True)
        if action in {"adjust", "refund"}:
            command.add_argument("--key", required=True)
            command.add_argument("--delta" if action == "adjust" else "--units", type=int, required=True)
    try:
        result = asyncio.run(run(parser.parse_args()))
    except ApplicationError as exc:
        print(json.dumps({"error": exc.code}))
        raise SystemExit(2) from None
    except Exception:
        print(json.dumps({"error": "quota_management_unavailable"}))
        raise SystemExit(1) from None
    print(json.dumps(result))

if __name__ == "__main__":
    main()
