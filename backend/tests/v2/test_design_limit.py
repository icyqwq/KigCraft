import pytest
from app.auth.models import UserRole
from app.core.config import get_settings
from app.core.time import utc_now
from app.db.models import User
from app.design.models import Design
from app.design.router import capabilities
from app.design.service import ApplicationError, create_design
from .test_migrations import migration_database
from .test_operations import factory, setup


@pytest.fixture
def limit(monkeypatch):
    def apply(value):
        monkeypatch.setenv("V2_DESIGN_LIMIT_PER_USER", str(value)); get_settings.cache_clear()
    return apply


def test_limit_defaults_to_ten_and_is_published_in_capabilities(monkeypatch, limit):
    monkeypatch.delenv("V2_DESIGN_LIMIT_PER_USER"); get_settings.cache_clear()
    assert get_settings().v2_design_limit_per_user == 10
    limit(30)
    assert get_settings().v2_design_limit_per_user == 30


async def test_capabilities_report_the_limit_or_none(limit):
    limit(12)
    assert (await capabilities()).design_limit == 12
    limit(0)
    assert (await capabilities()).design_limit is None


async def test_regular_account_keeps_at_most_the_configured_number(factory, limit):
    owner, first, _ = await setup(factory)
    limit(3)
    async with factory.begin() as db:
        await create_design(db, owner, "limit-request-2", "Two")
        await create_design(db, owner, "limit-request-3", "Three")
    async with factory.begin() as db:
        with pytest.raises(ApplicationError, match="design_limit_reached"):
            await create_design(db, owner, "limit-request-4", "Four")
    async with factory.begin() as db:
        # Replaying an accepted request is not a new conversation.
        assert (await create_design(db, owner, "limit-request-3", "Three")).title == "Three"
    async with factory.begin() as db:
        (await db.get(Design, first)).deleted_at = utc_now()
    async with factory.begin() as db:
        await create_design(db, owner, "limit-request-4", "Four")
        with pytest.raises(ApplicationError, match="design_limit_reached"):
            await create_design(db, owner, "limit-request-5", "Five")


async def test_archived_conversations_do_not_count(factory, limit):
    owner, first, _ = await setup(factory)
    limit(1)
    async with factory.begin() as db:
        (await db.get(Design, first)).archived_at = utc_now()
    async with factory.begin() as db:
        await create_design(db, owner, "after-archive", "Next")


async def test_administrators_and_legacy_imports_are_not_limited(factory, limit):
    owner, _, _ = await setup(factory)
    limit(1)
    async with factory.begin() as db:
        await create_design(db, owner, "legacy-group", "Imported", enforce_limit=False)
        (await db.get(User, owner)).role = UserRole.admin
    async with factory.begin() as db:
        await create_design(db, owner, "admin-extra", "Admin")
