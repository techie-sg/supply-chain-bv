"""The four workbook sheets, the two RAG document tables, conversations and preferences."""

from datetime import date, datetime
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector  # type: ignore[import-untyped]
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from database.session import Base


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint("distance_from_store_km >= 0", name="ck_zones_distance"),
        CheckConstraint(
            "avg_ride_min_dry >= 0 AND avg_ride_min_rain >= 0",
            name="ck_zones_ride_times",
        ),
        {"schema": "app"},
    )

    zone_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    zone_name: Mapped[str] = mapped_column(Text)
    distance_from_store_km: Mapped[float] = mapped_column(Float)
    avg_ride_min_dry: Mapped[float] = mapped_column(Float)
    avg_ride_min_rain: Mapped[float] = mapped_column(Float)


class Rider(Base):
    __tablename__ = "riders"
    __table_args__ = (
        PrimaryKeyConstraint("scenario_key", "rider_id", name="pk_riders"),
        CheckConstraint("hours_on_shift >= 0", name="ck_riders_shift_hours"),
        CheckConstraint("deliveries_today >= 0", name="ck_riders_deliveries"),
        CheckConstraint(
            "minutes_since_last_break >= 0",
            name="ck_riders_break_minutes",
        ),
        CheckConstraint(
            "eta_back_min IS NULL OR eta_back_min >= 0",
            name="ck_riders_eta_back",
        ),
        Index("ix_riders_scenario_store_status", "scenario_key", "store_id", "status"),
        {"schema": "app"},
    )

    scenario_key: Mapped[str] = mapped_column(String(64))
    rider_id: Mapped[str] = mapped_column(String(32))
    store_id: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    current_zone: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("app.zones.zone_id", ondelete="RESTRICT"),
    )
    hours_on_shift: Mapped[float] = mapped_column(Float)
    deliveries_today: Mapped[int] = mapped_column(Integer)
    minutes_since_last_break: Mapped[int] = mapped_column(Integer)
    eta_back_min: Mapped[float | None] = mapped_column(Float)
    employment_type: Mapped[str] = mapped_column(String(32))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        PrimaryKeyConstraint("scenario_key", "order_id", name="pk_orders"),
        ForeignKeyConstraint(
            ["scenario_key", "assigned_rider_id"],
            ["app.riders.scenario_key", "app.riders.rider_id"],
            name="fk_orders_assigned_rider",
            ondelete="RESTRICT",
        ),
        CheckConstraint("item_count > 0", name="ck_orders_item_count"),
        Index(
            "ix_orders_scenario_store_status_placed",
            "scenario_key",
            "store_id",
            "status",
            "placed_at",
        ),
        {"schema": "app"},
    )

    scenario_key: Mapped[str] = mapped_column(String(64))
    order_id: Mapped[str] = mapped_column(String(32))
    store_id: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    zone_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("app.zones.zone_id", ondelete="RESTRICT"),
    )
    item_count: Mapped[int] = mapped_column(Integer)
    has_frozen_items: Mapped[bool] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(32))
    assigned_rider_id: Mapped[str | None] = mapped_column(String(32))


class HourlyMetric(Base):
    """Imported history supplied by the workbook; raw delivery events are absent."""

    __tablename__ = "hourly_metrics"
    __table_args__ = (
        PrimaryKeyConstraint("store_id", "date", "hour", name="pk_hourly_metrics"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="ck_hourly_metrics_hour"),
        CheckConstraint(
            "orders >= 0 AND riders_online >= 0",
            name="ck_hourly_metrics_counts",
        ),
        CheckConstraint(
            "sla_10min_pct BETWEEN 0 AND 100",
            name="ck_hourly_metrics_sla",
        ),
        {"schema": "app"},
    )

    store_id: Mapped[str] = mapped_column(String(32))
    date: Mapped[date] = mapped_column(Date)
    hour: Mapped[int] = mapped_column(Integer)
    orders: Mapped[int] = mapped_column(Integer)
    avg_pick_pack_min: Mapped[float] = mapped_column(Float)
    avg_rider_wait_min: Mapped[float] = mapped_column(Float)
    avg_ride_min: Mapped[float] = mapped_column(Float)
    sla_10min_pct: Mapped[float] = mapped_column(Float)
    riders_online: Mapped[int] = mapped_column(Integer)
    rain_flag: Mapped[bool] = mapped_column(Boolean)


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint("file_hash", "version", name="uq_documents_hash_version"),
        CheckConstraint(
            "octet_length(file_hash) = 32",
            name="ck_documents_hash_length",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    file_name: Mapped[str] = mapped_column(Text)
    file_hash: Mapped[bytes] = mapped_column(LargeBinary)
    document_date: Mapped[date | None] = mapped_column(Date)
    version: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    metadata_: Mapped[dict] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        PrimaryKeyConstraint("document_id", "chunk_id", name="pk_document_chunks"),
        CheckConstraint("chunk_id >= 0", name="ck_document_chunks_id"),
        {"schema": "app"},
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("app.documents.id", ondelete="CASCADE"),
    )
    chunk_id: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector())


class Conversation(Base):
    """One chat; `messages` is an append-only list of who, what and when."""

    __tablename__ = "conversations"
    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(messages) = 'array'",
            name="ck_conversations_messages_array",
        ),
        Index(
            "ix_conversations_store_manager_updated",
            "store_id",
            "manager_id",
            "updated_at",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    store_id: Mapped[str] = mapped_column(String(32))
    manager_id: Mapped[str] = mapped_column(String(32))
    messages: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        default=list,
        server_default="[]",
    )
    summary: Mapped[str | None] = mapped_column(Text)
    summary_covers_to: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class PreferenceDefinition(Base):
    """A configurable catalogue item; seeded by migration, never edited by managers."""

    __tablename__ = "preference_definitions"
    __table_args__ = (
        CheckConstraint(
            "category IN ('alert', 'batching', 'incentive', 'briefing')",
            name="ck_preference_definitions_category",
        ),
        CheckConstraint(
            "value_type IN ('number', 'boolean', 'choice', 'view_list')",
            name="ck_preference_definitions_value_type",
        ),
        CheckConstraint(
            "unit IS NULL OR unit IN "
            "('orders_per_rider', 'orders', 'minutes', 'percent', 'inr')",
            name="ck_preference_definitions_unit",
        ),
        CheckConstraint(
            "operator IS NULL OR operator IN ('gt', 'gte', 'lt')",
            name="ck_preference_definitions_operator",
        ),
        CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_preference_definitions_bounds",
        ),
        {"schema": "app"},
    )

    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    category: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    value_type: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(24))
    operator: Mapped[str | None] = mapped_column(String(4))
    default_value: Mapped[object | None] = mapped_column(JSONB(none_as_null=True))
    min_value: Mapped[float | None] = mapped_column(Numeric(asdecimal=False))
    max_value: Mapped[float | None] = mapped_column(Numeric(asdecimal=False))
    allowed_values: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    default_enabled: Mapped[bool] = mapped_column(Boolean)
    default_cooldown_min: Mapped[int | None] = mapped_column(Integer)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class StorePreference(Base):
    """A manager's value for one catalogue item; changes supersede, never edit."""

    __tablename__ = "store_preferences"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'superseded', 'removed')",
            name="ck_store_preferences_status",
        ),
        CheckConstraint(
            "options IS NULL OR jsonb_typeof(options) = 'object'",
            name="ck_store_preferences_options",
        ),
        Index(
            "uq_store_preferences_active",
            "store_id",
            "manager_id",
            "code",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "ix_store_preferences_manager_status",
            "store_id",
            "manager_id",
            "status",
        ),
        {"schema": "app"},
    )

    id: Mapped[UUID] = mapped_column(
        primary_key=True,
        default=uuid4,
        server_default=func.gen_random_uuid(),
    )
    store_id: Mapped[str] = mapped_column(String(32))
    manager_id: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(
        String(48),
        ForeignKey("app.preference_definitions.code", ondelete="RESTRICT"),
    )
    enabled: Mapped[bool] = mapped_column(Boolean)
    value: Mapped[object | None] = mapped_column(JSONB(none_as_null=True))
    options: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    status: Mapped[str] = mapped_column(
        String(16),
        default="active",
        server_default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
