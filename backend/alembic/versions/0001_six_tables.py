"""Create the four workbook tables and two document tables.

Revision ID: 0001_six_tables
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0001_six_tables"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "zones",
        sa.Column("zone_id", sa.String(32), nullable=False),
        sa.Column("zone_name", sa.Text(), nullable=False),
        sa.Column("distance_from_store_km", sa.Float(), nullable=False),
        sa.Column("avg_ride_min_dry", sa.Float(), nullable=False),
        sa.Column("avg_ride_min_rain", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("zone_id"),
        sa.CheckConstraint("distance_from_store_km >= 0", name="ck_zones_distance"),
        sa.CheckConstraint(
            "avg_ride_min_dry >= 0 AND avg_ride_min_rain >= 0",
            name="ck_zones_ride_times",
        ),
        schema="app",
    )
    op.create_table(
        "riders",
        sa.Column("scenario_key", sa.String(64), nullable=False),
        sa.Column("rider_id", sa.String(32), nullable=False),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("current_zone", sa.String(32), nullable=False),
        sa.Column("hours_on_shift", sa.Float(), nullable=False),
        sa.Column("deliveries_today", sa.Integer(), nullable=False),
        sa.Column("minutes_since_last_break", sa.Integer(), nullable=False),
        sa.Column("eta_back_min", sa.Float(), nullable=True),
        sa.Column("employment_type", sa.String(32), nullable=False),
        sa.PrimaryKeyConstraint("scenario_key", "rider_id", name="pk_riders"),
        sa.ForeignKeyConstraint(
            ["current_zone"],
            ["app.zones.zone_id"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("hours_on_shift >= 0", name="ck_riders_shift_hours"),
        sa.CheckConstraint("deliveries_today >= 0", name="ck_riders_deliveries"),
        sa.CheckConstraint(
            "minutes_since_last_break >= 0",
            name="ck_riders_break_minutes",
        ),
        sa.CheckConstraint(
            "eta_back_min IS NULL OR eta_back_min >= 0",
            name="ck_riders_eta_back",
        ),
        schema="app",
    )
    op.create_index(
        "ix_riders_scenario_store_status",
        "riders",
        ["scenario_key", "store_id", "status"],
        schema="app",
    )
    op.create_table(
        "orders",
        sa.Column("scenario_key", sa.String(64), nullable=False),
        sa.Column("order_id", sa.String(32), nullable=False),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zone_id", sa.String(32), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("has_frozen_items", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("assigned_rider_id", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("scenario_key", "order_id", name="pk_orders"),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["app.zones.zone_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["scenario_key", "assigned_rider_id"],
            ["app.riders.scenario_key", "app.riders.rider_id"],
            name="fk_orders_assigned_rider",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("item_count > 0", name="ck_orders_item_count"),
        schema="app",
    )
    op.create_index(
        "ix_orders_scenario_store_status_placed",
        "orders",
        ["scenario_key", "store_id", "status", "placed_at"],
        schema="app",
    )
    op.create_table(
        "hourly_metrics",
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=False),
        sa.Column("avg_pick_pack_min", sa.Float(), nullable=False),
        sa.Column("avg_rider_wait_min", sa.Float(), nullable=False),
        sa.Column("avg_ride_min", sa.Float(), nullable=False),
        sa.Column("sla_10min_pct", sa.Float(), nullable=False),
        sa.Column("riders_online", sa.Integer(), nullable=False),
        sa.Column("rain_flag", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("store_id", "date", "hour", name="pk_hourly_metrics"),
        sa.CheckConstraint("hour BETWEEN 0 AND 23", name="ck_hourly_metrics_hour"),
        sa.CheckConstraint(
            "orders >= 0 AND riders_online >= 0",
            name="ck_hourly_metrics_counts",
        ),
        sa.CheckConstraint(
            "sla_10min_pct BETWEEN 0 AND 100",
            name="ck_hourly_metrics_sla",
        ),
        schema="app",
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_hash", sa.LargeBinary(), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "metadata",
            JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_hash", "version", name="uq_documents_hash_version"),
        sa.CheckConstraint(
            "octet_length(file_hash) = 32",
            name="ck_documents_hash_length",
        ),
        schema="app",
    )
    op.create_table(
        "document_chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.PrimaryKeyConstraint("document_id", "chunk_id", name="pk_document_chunks"),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["app.documents.id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("chunk_id >= 0", name="ck_document_chunks_id"),
        schema="app",
    )


def downgrade() -> None:
    for table in (
        "document_chunks",
        "documents",
        "hourly_metrics",
        "orders",
        "riders",
        "zones",
    ):
        op.drop_table(table, schema="app")
