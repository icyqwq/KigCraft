"""A25: real PostgreSQL transactions; no model calls or shared QA database."""
import asyncio
from collections import Counter
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.design.models import LedgerEntry, Wallet
from app.design.service import create_design
from app.generation.quota import refund_generation_quota
from app.operations.cancel import cancel
from app.operations.lifecycle import finish
from app.operations.publish import publish
from app.quota.adjustments import adjust, refund
from app.quota.policy import top_up
from app.quota.settings import QuotaPolicy
from app.quota.wallet import locked_wallet
from .test_migrations import migration_database
from .test_operations import factory, setup, quoted, request, submit_request, running, staged
from .test_quota_cutover import actors, job


@pytest.fixture
def clock(monkeypatch):
    policy = QuotaPolicy(daily_generation_limit=3, daily_generation_reset_hour=0)
    monkeypatch.setattr("app.quota.settings._quota_policy", policy)
    state = {"now": datetime(2026, 9, 27, 15, 59, 59, tzinfo=UTC), "policy": policy}
    monkeypatch.setattr("app.generation.quota.utc_now", lambda: state["now"])
    return state


async def another_operation(factory, owner, label):
    async with factory.begin() as db:
        branch = (await create_design(db, owner, "design-" + label, label)).last_branch_id
    return await submit_request(factory, owner, branch,
                                await quoted(factory, owner, branch, request(client_request_id="request-" + label)))


async def read_balance(factory, owner):
    async with factory.begin() as db:
        wallet = await locked_wallet(db, owner)
        await top_up(db, wallet)
        return wallet.balance, wallet.reserved


async def check_ledger(factory, owner, expected, counts):
    async with factory() as db:
        wallet = await db.get(Wallet, owner)
        rows = list(await db.scalars(select(LedgerEntry).where(LedgerEntry.owner_id == owner)))
        assert (wallet.balance, wallet.reserved) == expected
        assert (sum(row.balance_delta for row in rows), sum(row.reserved_delta for row in rows)) == expected
        assert all(row.balance_after >= row.reserved_after >= 0 for row in rows)
        assert len({row.idempotency_key for row in rows}) == len(rows)
        actual = Counter(row.kind for row in rows)
        for kind, count in counts.items():
            assert actual[kind] == count, actual
        return rows


@pytest.mark.parametrize("delivered", [0, 1, 2])
async def test_documented_two_holds_cross_midnight(factory, tmp_path, clock, delivered):
    owner, _, branch = await setup(factory, balance=3)
    first = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    second = await another_operation(factory, owner, "second")
    assert await read_balance(factory, owner) == (3, 2)
    clock["now"] += timedelta(seconds=1)  # Exact Shanghai midnight.
    assert await read_balance(factory, owner) == (3, 2)
    for index, opid in enumerate([first, second]):
        token = await running(factory, opid)
        async with factory.begin() as db:
            if index < delivered:
                await publish(db, opid, "worker", token, "main_image", staged(tmp_path),
                              storage_root=tmp_path / "objects", finalize=False)
                assert await finish(db, opid, "worker", token, upstream_status="completed") == "succeeded"
            else:
                assert await finish(db, opid, "worker", token, upstream_status="failed") == "failed"
        assert await read_balance(factory, owner) == (3 - min(index + 1, delivered), 1 - index)
    rows = await check_ledger(factory, owner, (3 - delivered, 0),
                             {"topup": 1, "reserve": 2, "settle": delivered, "release": 2, "refund": 0})
    assert [row.balance_delta for row in rows if row.kind == "topup"] == [0]


@pytest.mark.parametrize("reset_hour", [0, 6])
@pytest.mark.parametrize("balance", [2, 8])
async def test_concurrent_rollover_refund_gift_release_and_reads(factory, tmp_path, clock, balance, reset_hour):
    clock["policy"].daily_generation_reset_hour = reset_hour
    clock["now"] = datetime(2026, 9, 27, 16, tzinfo=UTC) + timedelta(hours=reset_hour, seconds=-1)
    owner, _, branch = await setup(factory, balance=balance + 1)
    _, admin = await actors(factory)
    paid = await submit_request(factory, owner, branch, await quoted(factory, owner, branch))
    token = await running(factory, paid)
    async with factory.begin() as db:
        await publish(db, paid, "worker", token, "main_image", staged(tmp_path),
                      storage_root=tmp_path / "objects", finalize=False)
        assert await finish(db, paid, "worker", token, upstream_status="completed") == "succeeded"
    cancelled = await another_operation(factory, owner, "cancel")
    await another_operation(factory, owner, "pending")
    legacy = await job(factory, owner)
    old_window = clock["now"]
    assert await read_balance(factory, owner) == (balance, 2)
    clock["now"] += timedelta(seconds=1)
    start = asyncio.Event()

    async def action(kind):
        await start.wait()
        async with factory.begin() as db:
            if kind == "gift":
                await adjust(db, owner, delta=4, key="rollover-gift", actor_id=admin, reason="Test gift")
            elif kind == "refund":
                await refund(db, paid, units=1, key="rollover-refund", actor_id=admin, reason="Test refund")
            elif kind == "legacy":
                assert await refund_generation_quota(db, owner, old_window, legacy_job_id=legacy)
            elif kind == "cancel":
                assert await cancel(db, owner, cancelled) == "cancelled"
            else:
                await top_up(db, await locked_wallet(db, owner))

    tasks = [asyncio.create_task(action(kind)) for kind in ["read", "gift", "refund", "cancel", "legacy"] * 3]
    start.set()
    await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)
    expected = max(balance, 3) + 6
    rows = await check_ledger(factory, owner, (expected, 1),
                             {"topup": 1, "reserve": 3, "settle": 1, "release": 2,
                              "adjust": 1, "refund": 1, "legacy_refund": 1})
    assert [row.balance_delta for row in rows if row.kind == "topup"] == [max(0, 3 - balance)]
    clock["now"] += timedelta(days=1)
    assert await read_balance(factory, owner) == (expected, 1)
    await check_ledger(factory, owner, (expected, 1), {"topup": 2, "adjust": 1, "refund": 1, "legacy_refund": 1})


async def test_rolled_back_first_topup_can_be_retried_concurrently(factory, clock):
    owner, _, _ = await setup(factory, balance=1)
    clock["now"] += timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="abort"):
        async with factory.begin() as db:
            await top_up(db, await locked_wallet(db, owner))
            raise RuntimeError("abort before commit")
    await check_ledger(factory, owner, (1, 0), {"topup": 0})
    assert await asyncio.gather(*(read_balance(factory, owner) for _ in range(8))) == [(3, 0)] * 8
    await check_ledger(factory, owner, (3, 0), {"topup": 1})


@pytest.mark.parametrize("cross_day", [False, True])
@pytest.mark.parametrize("path", ["adjust", "legacy_refund", "quota_read"])
async def test_wallet_lock_refreshes_a_previously_read_balance(factory, clock, path, cross_day):
    owner, _, _ = await setup(factory, balance=3)
    _, admin = await actors(factory)
    legacy = await job(factory, owner) if path == "legacy_refund" else None
    async with factory.begin() as stale:
        cached = await stale.get(Wallet, owner)  # Keep the identity-map object alive.
        assert cached.balance == 3
        async with factory.begin() as concurrent:
            await adjust(concurrent, owner, delta=4, key="external-gift", actor_id=admin, reason="Concurrent gift")
        if cross_day:
            clock["now"] += timedelta(seconds=1)
        if path == "adjust":
            await adjust(stale, owner, delta=2, key="later-gift", actor_id=admin, reason="Later gift")
        elif path == "legacy_refund":
            assert await refund_generation_quota(stale, owner, clock["now"], legacy_job_id=legacy)
        else:
            from app.db.models import User
            from app.design.router import quota_state
            response = await quota_state(current=await stale.get(User, owner), db=stale)
            assert (response["balance"], response["available"]) == (7, 7)
        expected = {"adjust": 9, "legacy_refund": 8, "quota_read": 7}[path]
        assert cached.balance == expected
    await check_ledger(factory, owner, (expected, 0),
                       {"adjust": 2 if path == "adjust" else 1,
                        "legacy_refund": int(path == "legacy_refund"), "topup": int(cross_day)})
