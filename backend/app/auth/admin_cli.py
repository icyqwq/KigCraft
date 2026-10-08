import argparse
import asyncio
import getpass

from sqlalchemy import select, update

from app.auth.email_validation import normalize_email_or_exit
from app.auth.models import UserRole
from app.auth.passwords import hash_password
from app.core.time import utc_now
from app.db.models import AuthSession, User
from app.db.session import ensure_database_schema, get_sessionmaker


async def create_or_update_admin(email: str, password: str) -> None:
    normalized = normalize_email_or_exit(email)
    await ensure_database_schema()
    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == normalized))
        user = result.scalar_one_or_none()
        if user is None:
            session.add(
                User(
                    email=normalized,
                    email_verified_at=utc_now(),
                    password_hash=hash_password(password),
                    role=UserRole.admin,
                )
            )
        else:
            user.role = UserRole.admin
            user.email_verified_at = user.email_verified_at or utc_now()
            user.password_hash = hash_password(password)
        await session.commit()


async def promote_admin(email: str) -> None:
    normalized = normalize_email_or_exit(email)
    await ensure_database_schema()
    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == normalized))
        user = result.scalar_one_or_none()
        if user is None:
            raise SystemExit(f"user_not_found:{normalized}")
        user.role = UserRole.admin
        await session.commit()


async def revoke_sessions(email: str) -> None:
    normalized = normalize_email_or_exit(email)
    await ensure_database_schema()
    async with get_sessionmaker()() as session:
        result = await session.execute(select(User).where(User.email == normalized))
        user = result.scalar_one_or_none()
        if user is None:
            raise SystemExit(f"user_not_found:{normalized}")
        await session.execute(
            update(AuthSession)
            .where(AuthSession.user_id == user.id)
            .where(AuthSession.revoked_at.is_(None))
            .values(revoked_at=utc_now())
        )
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.auth.admin_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create-admin")
    create_parser.add_argument("--email", required=True)

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--email", required=True)

    revoke_parser = subparsers.add_parser("revoke-sessions")
    revoke_parser.add_argument("--email", required=True)

    args = parser.parse_args()
    if args.command == "create-admin":
        password = getpass.getpass("Admin password: ")
        if len(password) < 8:
            raise SystemExit("password_too_short")
        asyncio.run(create_or_update_admin(args.email, password))
    elif args.command == "promote":
        asyncio.run(promote_admin(args.email))
    elif args.command == "revoke-sessions":
        asyncio.run(revoke_sessions(args.email))


if __name__ == "__main__":
    main()
