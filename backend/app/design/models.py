"""V2 tables are created ONLY by controlled migrations, never startup create_all."""
from __future__ import annotations
from datetime import datetime
from typing import Any
from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
    Index, Integer, JSON, String, Text, UniqueConstraint, FetchedValue, event as orm_event,
)
from sqlalchemy.orm import Mapped, mapped_column
from app.core.time import utc_now
from app.db.base import Base
from app.db.models import uuid_str

V2 = {"info": {"v2": True}}


class Design(Base):
    __tablename__ = "v2_designs"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_request_id", name="uq_v2_design_request"),
        UniqueConstraint("id", "owner_id", name="uq_v2_design_owner"),
        CheckConstraint("trash_restore_until IS NULL OR (deleted_at IS NOT NULL AND trash_restore_until > deleted_at)", name="ck_v2_design_trash_deadline"),
        Index("ix_v2_design_trash_restore_until", "trash_restore_until"),
        CheckConstraint("content_purged_at IS NULL OR (deleted_at IS NOT NULL AND trash_restore_until IS NOT NULL AND content_purged_at >= trash_restore_until AND retained_resources IS NOT NULL)", name="ck_v2_design_content_retirement"),
        CheckConstraint("revision_counter >= 0 AND revision_counter <= 9007199254740991", name="ck_v2_design_revision_counter"),
        CheckConstraint("history_counter >= 0 AND history_counter <= 9007199254740991", name="ck_v2_design_history_counter"),
        ForeignKeyConstraint(["last_branch_id", "id"], ["v2_branches.id", "v2_branches.design_id"],
                             name="fk_v2_design_last_branch", use_alter=True), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_digest: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200), default="")
    metadata_version: Mapped[int] = mapped_column(Integer, default=0)
    event_sequence: Mapped[int] = mapped_column(BigInteger, default=0)
    revision_counter: Mapped[int] = mapped_column(BigInteger, server_default="0")
    history_counter: Mapped[int] = mapped_column(BigInteger, server_default="0")
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    list_sequence: Mapped[int] = mapped_column(BigInteger, server_default=FetchedValue())
    last_branch_id: Mapped[str | None] = mapped_column(String(36))
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    trash_restore_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retained_resources: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Branch(Base):
    __tablename__ = "v2_branches"
    __table_args__ = (
        UniqueConstraint("id", "design_id", name="uq_v2_branch_design"),
        CheckConstraint("state_version >= 0", name="ck_v2_branch_version"),
        CheckConstraint("history_parent_sequence >= 0 AND history_parent_sequence <= 9007199254740991", name="ck_v2_branch_history_parent"),
        ForeignKeyConstraint(["origin_revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"],
                             name="fk_v2_branch_origin", use_alter=True),
        ForeignKeyConstraint(["parent_branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"],
                             name="fk_v2_branch_parent"),
        ForeignKeyConstraint(["current_revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"],
                             name="fk_v2_branch_current", use_alter=True), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    design_id: Mapped[str] = mapped_column(ForeignKey("v2_designs.id"), index=True)
    parent_branch_id: Mapped[str | None] = mapped_column(ForeignKey("v2_branches.id"))
    context_seed: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    origin_revision_id: Mapped[str | None] = mapped_column(String(36))
    fork_boundary_message_id: Mapped[str | None] = mapped_column(String(36))
    history_parent_sequence: Mapped[int | None] = mapped_column(BigInteger)
    current_revision_id: Mapped[str | None] = mapped_column(String(36))
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    active_epoch_id: Mapped[str | None] = mapped_column(ForeignKey("v2_codex_epochs.id", use_alter=True, name="fk_v2_branch_epoch"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Asset(Base):
    __tablename__ = "v2_assets"
    __table_args__ = (
        UniqueConstraint("object_key", name="uq_v2_asset_object"),
        UniqueConstraint("id", "owner_id", name="uq_v2_asset_owner"),
        CheckConstraint("width > 0 AND height > 0 AND byte_size > 0", name="ck_v2_asset_dimensions"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    mime_type: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(32))
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AssetReviewCache(Base):
    __tablename__ = "v2_asset_review_cache"
    __table_args__ = (
        ForeignKeyConstraint(["source_operation_id", "owner_id"],
            ["v2_operations.id", "v2_operations.owner_id"], name="fk_v2_review_cache_source_owner"),
        CheckConstraint("verdict IN ('allow','reject')", name="ck_v2_review_cache_verdict"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$' AND policy_key ~ '^[0-9a-f]{64}$'", name="ck_v2_review_cache_hashes"),
        V2,
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    verdict: Mapped[str] = mapped_column(String(8))
    source_operation_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InputRecord(Base):
    __tablename__ = "v2_input_snapshots"
    __table_args__ = V2
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    branch_id: Mapped[str] = mapped_column(ForeignKey("v2_branches.id"), index=True)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    digest: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Wallet(Base):
    __tablename__ = "v2_wallets"
    __table_args__ = (
        CheckConstraint("balance >= reserved AND reserved >= 0", name="ck_v2_wallet_balances"),
        CheckConstraint("version >= 0", name="ck_v2_wallet_version"), V2,
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=0)
    # Accounts must be explicitly cut over before this wallet can mutate balance.
    write_owner: Mapped[str] = mapped_column(String(16), default="legacy")
    last_topup_window: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Quote(Base):
    __tablename__ = "v2_quotes"
    __table_args__ = (
        CheckConstraint("max_units >= 0", name="ck_v2_quote_units"),
        UniqueConstraint("id", "owner_id", name="uq_v2_quote_owner"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    scope_digest: Mapped[str] = mapped_column(String(64))
    operation_kind: Mapped[str] = mapped_column(String(32))
    max_units: Mapped[int] = mapped_column(Integer)
    deliverables: Mapped[dict[str, int]] = mapped_column(JSON)
    policy_version: Mapped[str] = mapped_column(String(50))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Operation(Base):
    __tablename__ = "v2_operations"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_request_id", name="uq_v2_operation_request"),
        UniqueConstraint("quote_id", name="uq_v2_operation_quote"),
        UniqueConstraint("id", "owner_id", name="uq_v2_operation_owner"),
        UniqueConstraint("id", "design_id", name="uq_v2_operation_design"),
        ForeignKeyConstraint(["quote_id", "owner_id"], ["v2_quotes.id", "v2_quotes.owner_id"],
                             name="fk_v2_operation_quote_owner"),
        ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"],
                             name="fk_v2_operation_branch_design"),
        ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"],
                             name="fk_v2_operation_design_owner"),
        CheckConstraint("fencing_token >= 0 AND reserved_units >= 0 AND settled_units >= 0", name="ck_v2_operation_units"),
        CheckConstraint("settled_units <= reserved_units", name="ck_v2_operation_cap"),
        Index("ix_v2_operation_queue", "state", "lease_expires_at", "created_at"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    design_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_digest: Mapped[str] = mapped_column(String(64))
    input_snapshot_id: Mapped[str] = mapped_column(ForeignKey("v2_input_snapshots.id"))
    quote_id: Mapped[str | None] = mapped_column(ForeignKey("v2_quotes.id"))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    expected_branch_version: Mapped[int] = mapped_column(Integer)
    base_revision_id: Mapped[str | None] = mapped_column(String(36))
    reserved_units: Mapped[int] = mapped_column(Integer, default=0)
    settled_units: Mapped[int] = mapped_column(Integer, default=0)
    reservation_closed: Mapped[bool] = mapped_column(Boolean, default=False)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(100))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    question: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))
    context_allocation: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source_operation_id: Mapped[str | None] = mapped_column(ForeignKey("v2_operations.id", name="fk_v2_operation_source"))
    context_before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    upstream_thread_id: Mapped[str | None] = mapped_column(String(100))
    upstream_turn_id: Mapped[str | None] = mapped_column(String(100))
    upstream_status: Mapped[str | None] = mapped_column(String(24))
    staged_outputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    dispatch_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Revision(Base):
    __tablename__ = "v2_revisions"
    __table_args__ = (
        UniqueConstraint("id", "design_id", name="uq_v2_revision_design"),
        UniqueConstraint("design_id", "number", name="uq_v2_revision_number"),
        Index("ix_v2_revisions_branch_history", "branch_id", "history_sequence", unique=True),
        CheckConstraint("history_sequence >= 1 AND history_sequence <= 9007199254740991", name="ck_v2_revisions_history_sequence"),
        CheckConstraint("number >= 1 AND number <= 9007199254740991", name="ck_v2_revision_number"),
        CheckConstraint("image_kind IN ('head_shell','reference')", name="ck_v2_revisions_image_kind"),
        ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"],
                             name="fk_v2_revision_branch_design"),
        UniqueConstraint("operation_id", "delivery_key", name="uq_v2_revision_delivery"),
        UniqueConstraint("branch_id", "client_request_id", name="uq_v2_manual_request"),
        UniqueConstraint("design_id", "client_request_id", name="uq_v2_manual_design_request"),
        ForeignKeyConstraint(["parent_revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"],
                             name="fk_v2_revision_parent"),
        ForeignKeyConstraint(["operation_id", "design_id"], ["v2_operations.id", "v2_operations.design_id"],
                             name="fk_v2_revision_operation"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    design_id: Mapped[str] = mapped_column(ForeignKey("v2_designs.id"), index=True)
    branch_id: Mapped[str] = mapped_column(String(36))
    parent_revision_id: Mapped[str | None] = mapped_column(ForeignKey("v2_revisions.id"))
    clean_asset_id: Mapped[str] = mapped_column(ForeignKey("v2_assets.id"))
    root_source_asset_id: Mapped[str] = mapped_column(ForeignKey("v2_assets.id"))
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("v2_operations.id"))
    client_request_id: Mapped[str | None] = mapped_column(String(100))
    delivery_key: Mapped[str | None] = mapped_column(String(100))
    number: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=FetchedValue())
    history_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=FetchedValue())
    source_kind: Mapped[str] = mapped_column(String(24))
    view: Mapped[str] = mapped_column(String(24), default="front")
    # head_shell: finished head-shell photo; reference: character art used as a reference. Fixed at insert (I03).
    image_kind: Mapped[str] = mapped_column(String(16), nullable=False, default="head_shell", server_default="head_shell")
    request_digest: Mapped[str | None] = mapped_column(String(64))
    context_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recipe: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class LedgerEntry(Base):
    __tablename__ = "v2_ledger"
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_v2_ledger_key"),
        ForeignKeyConstraint(["operation_id", "owner_id"], ["v2_operations.id", "v2_operations.owner_id"],
                             name="fk_v2_ledger_operation_owner"),
        CheckConstraint("balance_after >= reserved_after AND reserved_after >= 0", name="ck_v2_ledger_balances"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("v2_wallets.owner_id"), index=True)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("v2_operations.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    balance_delta: Mapped[int] = mapped_column(Integer)
    reserved_delta: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    reserved_after: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Message(Base):
    __tablename__ = "v2_messages"
    __table_args__ = (
        UniqueConstraint("operation_id", "source_item_id", name="uq_v2_message_source"),
        Index("ix_v2_messages_branch_history", "branch_id", "history_sequence", unique=True),
        CheckConstraint("history_sequence >= 1 AND history_sequence <= 9007199254740991", name="ck_v2_messages_history_sequence"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    branch_id: Mapped[str] = mapped_column(ForeignKey("v2_branches.id"), index=True)
    operation_id: Mapped[str | None] = mapped_column(ForeignKey("v2_operations.id"))
    history_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=FetchedValue())
    role: Mapped[str] = mapped_column(String(16))
    content_parts: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    search_text: Mapped[str] = mapped_column(Text, default="")
    source_item_id: Mapped[str | None] = mapped_column(String(100))
    source_turn_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="completed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


@orm_event.listens_for(Message, "before_insert")
@orm_event.listens_for(Message, "before_update")
def index_public_message(_mapper, _connection, target):
    from app.design.search_text import message_search_text
    target.search_text = message_search_text(target.role, target.content_parts)


class DomainEvent(Base):
    __tablename__ = "v2_events"
    __table_args__ = (
        UniqueConstraint("design_id", "sequence", name="uq_v2_event_sequence"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    design_id: Mapped[str] = mapped_column(ForeignKey("v2_designs.id"), index=True)
    sequence: Mapped[int] = mapped_column(BigInteger)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CodexEpoch(Base):
    __tablename__ = "v2_codex_epochs"
    __table_args__ = (UniqueConstraint("runtime_identity", "thread_id", name="uq_v2_epoch_thread"), V2)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    branch_id: Mapped[str] = mapped_column(ForeignKey("v2_branches.id"), index=True)
    thread_id: Mapped[str] = mapped_column(String(100))
    runtime_identity: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(100))
    codex_version: Mapped[str] = mapped_column(String(100))
    checkpoint_turn_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)



class ViewSet(Base):
    __tablename__ = "v2_viewsets"
    __table_args__ = (
        UniqueConstraint("owner_id", "client_request_id", name="uq_v2_viewset_request"),
        UniqueConstraint("quote_id", name="uq_v2_viewset_quote"),
        UniqueConstraint("id", "design_id", name="uq_v2_viewset_design"),
        ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"], name="fk_v2_viewset_owner"),
        ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"], name="fk_v2_viewset_branch"),
        ForeignKeyConstraint(["source_revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_viewset_source"),
        ForeignKeyConstraint(["quote_id", "owner_id"], ["v2_quotes.id", "v2_quotes.owner_id"], name="fk_v2_viewset_quote"),
        CheckConstraint("max_units >= settled_units + held_units AND settled_units >= 0 AND held_units >= 0 AND run_number >= 1", name="ck_v2_viewset_units"),
        V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(String(36))
    design_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    source_revision_id: Mapped[str] = mapped_column(String(36))
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_digest: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    snapshot_digest: Mapped[str] = mapped_column(String(64))
    quote_id: Mapped[str] = mapped_column(String(36))
    max_units: Mapped[int] = mapped_column(Integer)
    settled_units: Mapped[int] = mapped_column(Integer, default=0)
    held_units: Mapped[int] = mapped_column(Integer, default=0)
    run_number: Mapped[int] = mapped_column(Integer, default=1)
    authorization_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(24), default="running")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ViewSetItem(Base):
    __tablename__ = "v2_viewset_items"
    __table_args__ = (
        UniqueConstraint("viewset_id", "view", name="uq_v2_viewset_angle"),
        UniqueConstraint("viewset_id", "ordinal", name="uq_v2_viewset_ordinal"),
        UniqueConstraint("id", "design_id", name="uq_v2_viewset_item_design"),
        ForeignKeyConstraint(["viewset_id", "design_id"], ["v2_viewsets.id", "v2_viewsets.design_id"], name="fk_v2_viewset_item_group"),
        ForeignKeyConstraint(["revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_viewset_item_revision"),
        CheckConstraint("unit_cost >= 0 AND ordinal >= 0 AND attempt_count >= 0", name="ck_v2_viewset_item_units"),
        V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    viewset_id: Mapped[str] = mapped_column(String(36))
    design_id: Mapped[str] = mapped_column(String(36))
    view: Mapped[str] = mapped_column(String(24))
    ordinal: Mapped[int] = mapped_column(Integer)
    unit_cost: Mapped[int] = mapped_column(Integer)
    inferred: Mapped[bool] = mapped_column(Boolean)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    revision_id: Mapped[str | None] = mapped_column(String(36))
    error_code: Mapped[str | None] = mapped_column(String(100))


class ViewSetAttempt(Base):
    __tablename__ = "v2_viewset_attempts"
    __table_args__ = (
        UniqueConstraint("item_id", "number", name="uq_v2_viewset_attempt"),
        ForeignKeyConstraint(["item_id", "design_id"], ["v2_viewset_items.id", "v2_viewset_items.design_id"], name="fk_v2_viewset_attempt_item"),
        ForeignKeyConstraint(["operation_id", "design_id"], ["v2_operations.id", "v2_operations.design_id"], name="fk_v2_viewset_attempt_operation"),
        CheckConstraint("number >= 1", name="ck_v2_viewset_attempt_number"), V2,
    )
    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(36))
    design_id: Mapped[str] = mapped_column(String(36))
    number: Mapped[int] = mapped_column(Integer)


class EditorDraft(Base):
    __tablename__ = "v2_editor_drafts"
    __table_args__ = (
        ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"], name="fk_v2_draft_design_owner"),
        ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"], name="fk_v2_draft_branch"),
        ForeignKeyConstraint(["base_revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_draft_base"),
        ForeignKeyConstraint(["root_asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"], name="fk_v2_draft_root_owner"),
        CheckConstraint("version >= 1", name="ck_v2_draft_version"),
        Index("ix_v2_draft_scope", "owner_id", "branch_id", "base_revision_id"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36))
    design_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    base_revision_id: Mapped[str] = mapped_column(String(36))
    root_asset_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    device_id: Mapped[str] = mapped_column(String(100))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DraftWriteReceipt(Base):
    __tablename__ = "v2_draft_write_receipts"
    __table_args__ = V2
    draft_id: Mapped[str] = mapped_column(ForeignKey("v2_editor_drafts.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    result_version: Mapped[int] = mapped_column(Integer)
    deleted: Mapped[bool] = mapped_column(Boolean)


class EditorCheckpoint(Base):
    __tablename__ = "v2_editor_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"], name="fk_v2_editor_checkpoint_owner"),
        ForeignKeyConstraint(["branch_id", "design_id"], ["v2_branches.id", "v2_branches.design_id"], name="fk_v2_editor_checkpoint_branch"),
        ForeignKeyConstraint(["revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_editor_checkpoint_revision"),
        ForeignKeyConstraint(["root_asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"], name="fk_v2_editor_checkpoint_root"),
        ForeignKeyConstraint(["clean_asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"], name="fk_v2_editor_checkpoint_clean"),
        UniqueConstraint("branch_id", "revision_id", "version", name="uq_v2_editor_checkpoint_version"),
        UniqueConstraint("id", "design_id", name="uq_v2_editor_checkpoint_design"),
        CheckConstraint("version >= 1", name="ck_v2_editor_checkpoint_version"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(String(36))
    design_id: Mapped[str] = mapped_column(String(36))
    branch_id: Mapped[str] = mapped_column(String(36))
    revision_id: Mapped[str] = mapped_column(String(36))
    root_asset_id: Mapped[str] = mapped_column(String(36))
    clean_asset_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict[str, Any]] = mapped_column(JSON)
    document_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class EditorSaveReceipt(Base):
    __tablename__ = "v2_editor_save_receipts"
    __table_args__ = (
        ForeignKeyConstraint(["checkpoint_id", "design_id"], ["v2_editor_checkpoints.id", "v2_editor_checkpoints.design_id"], name="fk_v2_editor_receipt_checkpoint"),
        CheckConstraint("disposition IN ('revision', 'checkpoint', 'unchanged')", name="ck_v2_editor_receipt_disposition"), V2,
    )
    design_id: Mapped[str] = mapped_column(ForeignKey("v2_designs.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    request_digest: Mapped[str] = mapped_column(String(64))
    checkpoint_id: Mapped[str] = mapped_column(String(36))
    disposition: Mapped[str] = mapped_column(String(24))


class RuntimeControl(Base):
    __tablename__ = "v2_runtime_controls"
    __table_args__ = V2
    scope: Mapped[str] = mapped_column(String(50), primary_key=True)
    blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)


class AdminAction(Base):
    __tablename__ = "v2_admin_actions"
    __table_args__ = (
        UniqueConstraint("actor_id","client_request_id",name="uq_v2_admin_request"),
        CheckConstraint("replay_count >= 0",name="ck_v2_admin_replays"),
        Index("ix_v2_admin_pending","owner_id","status","created_at"),
        V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    client_request_id: Mapped[str] = mapped_column(String(100))
    request_digest: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(24))
    target_id: Mapped[str] = mapped_column(String(36))
    owner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str] = mapped_column(String(500))
    units: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(100))
    replay_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RecoveryCheck(Base):
    __tablename__ = "v2_recovery_checks"
    __table_args__ = (
        UniqueConstraint("operation_id","attempt",name="uq_v2_recovery_attempt"),
        CheckConstraint("attempt > 0",name="ck_v2_recovery_attempt"),
        Index("ix_v2_recovery_operation","operation_id","attempt"),
        V2,
    )
    id: Mapped[str] = mapped_column(String(36),primary_key=True,default=uuid_str)
    operation_id: Mapped[str] = mapped_column(ForeignKey("v2_operations.id"))
    attempt: Mapped[int] = mapped_column(Integer)
    worker_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(24),default="running")
    operation_state: Mapped[str | None] = mapped_column(String(24))
    error_code: Mapped[str | None] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LegacyImport(Base):
    """Immutable source-to-V2 mapping; never a fabricated model operation."""
    __tablename__ = "v2_legacy_imports"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_v2_legacy_source"),
        Index("ix_v2_legacy_design", "design_id", "id"),
        Index("ix_v2_legacy_revision", "revision_id", "id"),
        ForeignKeyConstraint(["design_id", "owner_id"], ["v2_designs.id", "v2_designs.owner_id"], name="fk_v2_legacy_design_owner"),
        ForeignKeyConstraint(["revision_id", "design_id"], ["v2_revisions.id", "v2_revisions.design_id"], name="fk_v2_legacy_revision_design"),
        CheckConstraint("source_kind IN (\'job\',\'output\',\'album\')", name="ck_v2_legacy_kind"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_kind: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(36))
    source_digest: Mapped[str] = mapped_column(String(64))
    source_summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    group_key: Mapped[str] = mapped_column(String(64), index=True)
    design_id: Mapped[str] = mapped_column(String(36))
    revision_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SubmissionAdmission(Base):
    """Accepted public model-work requests; automatic view steps do not add admissions."""
    __tablename__ = "v2_submission_admissions"
    __table_args__ = (
        UniqueConstraint("owner_id", "action", "client_request_id", name="uq_v2_submission_admission"),
        CheckConstraint("action IN ('turn','viewset','viewset_retry')", name="ck_v2_submission_action"),
        Index("ix_v2_submission_window", "owner_id", "created_at"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20))
    client_request_id: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeObservation(Base):
    __tablename__ = "v2_runtime_observations"
    __table_args__ = (
        UniqueConstraint("operation_id","phase","fencing_token",name="uq_v2_runtime_observation_attempt"),
        CheckConstraint("phase IN ('review','generation')",name="ck_v2_observation_phase"),
        CheckConstraint("state IN ('dispatch_intent','started','completed','interrupted','failed')",name="ck_v2_observation_state"),
        CheckConstraint("token_status IN ('unknown','observed','invalid')",name="ck_v2_observation_tokens"),
        CheckConstraint("fencing_token >= 0",name="ck_v2_observation_fence"),
        Index("ix_v2_observation_operation","operation_id"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    operation_id: Mapped[str] = mapped_column(ForeignKey("v2_operations.id"))
    phase: Mapped[str] = mapped_column(String(16))
    fencing_token: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(100))
    codex_version: Mapped[str] = mapped_column(String(100))
    state: Mapped[str] = mapped_column(String(24))
    thread_id: Mapped[str] = mapped_column(String(100))
    turn_id: Mapped[str | None] = mapped_column(String(100))
    token_status: Mapped[str] = mapped_column(String(16))
    token_snapshot: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    image_items: Mapped[dict] = mapped_column(JSON)
    image_anomalies: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

class LegacyMaterial(Base):
    """Immutable imported references and uninterpreted original project drafts."""
    __tablename__ = "v2_legacy_materials"
    __table_args__ = (
        UniqueConstraint("source_kind", "source_id", name="uq_v2_legacy_material_source"),
        ForeignKeyConstraint(["asset_id", "owner_id"], ["v2_assets.id", "v2_assets.owner_id"],
                             name="fk_v2_legacy_material_asset_owner"),
        CheckConstraint("source_kind IN ('reference','project_reference','draft')", name="ck_v2_legacy_material_kind"),
        CheckConstraint("(source_kind = 'draft' AND asset_id IS NULL) OR (source_kind <> 'draft' AND asset_id IS NOT NULL)",
                        name="ck_v2_legacy_material_payload"),
        Index("ix_v2_legacy_material_owner", "owner_id", "id"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    source_kind: Mapped[str] = mapped_column(String(20))
    source_id: Mapped[str] = mapped_column(String(36))
    source_digest: Mapped[str] = mapped_column(String(64))
    source_summary: Mapped[dict[str, Any]] = mapped_column(JSON)
    asset_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ContextObservation(Base):
    __tablename__ = "v2_context_observations"
    __table_args__ = (
        UniqueConstraint("operation_id", "fencing_token", name="uq_v2_context_attempt"),
        CheckConstraint("fencing_token >= 0", name="ck_v2_context_fence"),
        CheckConstraint("method IN ('start','resume','fork')", name="ck_v2_context_method"),
        CheckConstraint("state IN ('sent','confirmed','unconfirmed')", name="ck_v2_context_state"),
        CheckConstraint("(state = 'sent' AND completed_at IS NULL) OR (state <> 'sent' AND completed_at IS NOT NULL)",
                        name="ck_v2_context_completed"),
        Index("ix_v2_context_operation", "operation_id"), V2,
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    operation_id: Mapped[str] = mapped_column(ForeignKey("v2_operations.id"))
    fencing_token: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AccountClosure(Base):
    """Durable account deletion intent; identity and financial foreign keys survive."""
    __tablename__ = "v2_account_closures"
    __table_args__ = (
        CheckConstraint("state IN ('pending','cancelled','closed')", name="ck_v2_account_closure_state"),
        CheckConstraint("policy_days >= 1 AND policy_days <= 3650 AND delete_after > requested_at",
                        name="ck_v2_account_closure_policy"),
        CheckConstraint("(state = 'pending' AND completed_at IS NULL AND cancelled_at IS NULL) OR "
                        "(state = 'cancelled' AND completed_at IS NULL AND cancelled_at IS NOT NULL) OR "
                        "(state = 'closed' AND completed_at IS NOT NULL AND completed_at >= delete_after AND cancelled_at IS NULL)",
                        name="ck_v2_account_closure_terminal"),
        UniqueConstraint("request_id", name="uq_v2_account_closure_request"), V2,
    )
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(16))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delete_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    previously_banned: Mapped[bool] = mapped_column(Boolean)
    policy_days: Mapped[int] = mapped_column(Integer)


class AccountContentRetirement(Base):
    """Immutable resource references after closed-account legacy content removal."""
    __tablename__ = "v2_account_content_retirements"
    __table_args__ = (V2,)
    owner_id: Mapped[str] = mapped_column(ForeignKey("v2_account_closures.owner_id"), primary_key=True)
    source_digest: Mapped[str] = mapped_column(String(64))
    resources: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
