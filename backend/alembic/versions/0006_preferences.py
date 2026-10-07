"""Add the preference catalogue and per-manager preference values."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from alembic import op

revision: str = "0006_preferences"
down_revision: str | Sequence[str] | None = "0005_conversations"

# The catalogue as of this migration. Later changes belong in new migrations.
DEFINITIONS: list[dict] = [
    {
        "code": "rider_shortage_alert",
        "category": "alert",
        "name": "Rider shortage",
        "description": "Alerts when packed orders waiting for a rider per available "
        "rider goes above the threshold. With no available rider, it fires "
        "whenever an order is waiting.",
        "value_type": "number",
        "unit": "orders_per_rider",
        "operator": "gt",
        "default_value": 2,
        "min_value": 0.5,
        "max_value": 2,
        "allowed_values": None,
        "default_enabled": True,
        "default_cooldown_min": 15,
        "locked": False,
    },
    {
        "code": "orders_piling_up_alert",
        "category": "alert",
        "name": "Orders piling up",
        "description": "Alerts when orders not yet out for delivery (being picked, "
        "or packed and waiting for a rider) reach the threshold.",
        "value_type": "number",
        "unit": "orders",
        "operator": "gte",
        "default_value": 8,
        "min_value": 1,
        "max_value": 50,
        "allowed_values": None,
        "default_enabled": True,
        "default_cooldown_min": 15,
        "locked": False,
    },
    {
        "code": "order_waiting_too_long_alert",
        "category": "alert",
        "name": "Order waiting too long",
        "description": "Alerts when the oldest packed order has waited longer than "
        "the threshold.",
        "value_type": "number",
        "unit": "minutes",
        "operator": "gt",
        "default_value": 8,
        "min_value": 1,
        "max_value": 8,
        "allowed_values": None,
        "default_enabled": True,
        "default_cooldown_min": 10,
        "locked": False,
    },
    {
        "code": "frozen_order_waiting_alert",
        "category": "alert",
        "name": "Frozen order waiting",
        "description": "Alerts when the oldest packed order with frozen items has "
        "waited longer than the threshold.",
        "value_type": "number",
        "unit": "minutes",
        "operator": "gt",
        "default_value": 5,
        "min_value": 1,
        "max_value": 8,
        "allowed_values": None,
        "default_enabled": False,
        "default_cooldown_min": 10,
        "locked": False,
    },
    {
        "code": "sla_dip_alert",
        "category": "alert",
        "name": "SLA dip",
        "description": "Alerts when the 10-minute SLA for the last completed hour "
        "falls below the threshold.",
        "value_type": "number",
        "unit": "percent",
        "operator": "lt",
        "default_value": 80,
        "min_value": 50,
        "max_value": 100,
        "allowed_values": None,
        "default_enabled": False,
        "default_cooldown_min": 60,
        "locked": False,
    },
    {
        "code": "cold_chain_isolation",
        "category": "batching",
        "name": "Cold-chain isolation",
        "description": "Frozen and ice-cream orders are always delivered alone, "
        "never batched. This is store policy and cannot be changed.",
        "value_type": "boolean",
        "unit": None,
        "operator": None,
        "default_value": True,
        "min_value": None,
        "max_value": None,
        "allowed_values": None,
        "default_enabled": True,
        "default_cooldown_min": None,
        "locked": True,
    },
    {
        "code": "surge_only_batching",
        "category": "batching",
        "name": "Batch only when short of riders",
        "description": "When on, batches are proposed only while the rider shortage "
        "condition holds or a surge is declared.",
        "value_type": "boolean",
        "unit": None,
        "operator": None,
        "default_value": False,
        "min_value": None,
        "max_value": None,
        "allowed_values": None,
        "default_enabled": False,
        "default_cooldown_min": None,
        "locked": False,
    },
    {
        "code": "incentive_cap",
        "category": "incentive",
        "name": "Surge incentive cap per shift",
        "description": "The most the manager will spend on surge incentives in one "
        "shift. No incentive is proposed while it is unset.",
        "value_type": "number",
        "unit": "inr",
        "operator": None,
        "default_value": None,
        "min_value": 0,
        "max_value": 500,
        "allowed_values": None,
        "default_enabled": False,
        "default_cooldown_min": None,
        "locked": False,
    },
    {
        "code": "briefing",
        "category": "briefing",
        "name": "Greeting briefing",
        "description": "What to show when the manager greets the assistant.",
        "value_type": "view_list",
        "unit": None,
        "operator": None,
        "default_value": ["rider_stats", "order_queue"],
        "min_value": None,
        "max_value": None,
        "allowed_values": [
            "rider_stats",
            "order_queue",
            "oldest_order_age",
            "last_handover_note",
        ],
        "default_enabled": True,
        "default_cooldown_min": None,
        "locked": False,
    },
]


def upgrade() -> None:
    definitions = op.create_table(
        "preference_definitions",
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("value_type", sa.String(16), nullable=False),
        sa.Column("unit", sa.String(24), nullable=True),
        sa.Column("operator", sa.String(4), nullable=True),
        sa.Column("default_value", JSONB(none_as_null=True), nullable=True),
        sa.Column("min_value", sa.Numeric(), nullable=True),
        sa.Column("max_value", sa.Numeric(), nullable=True),
        sa.Column("allowed_values", ARRAY(sa.Text()), nullable=True),
        sa.Column("default_enabled", sa.Boolean(), nullable=False),
        sa.Column("default_cooldown_min", sa.Integer(), nullable=True),
        sa.Column(
            "locked",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("code"),
        sa.CheckConstraint(
            "category IN ('alert', 'batching', 'incentive', 'briefing')",
            name="ck_preference_definitions_category",
        ),
        sa.CheckConstraint(
            "value_type IN ('number', 'boolean', 'choice', 'view_list')",
            name="ck_preference_definitions_value_type",
        ),
        sa.CheckConstraint(
            "unit IS NULL OR unit IN "
            "('orders_per_rider', 'orders', 'minutes', 'percent', 'inr')",
            name="ck_preference_definitions_unit",
        ),
        sa.CheckConstraint(
            "operator IS NULL OR operator IN ('gt', 'gte', 'lt')",
            name="ck_preference_definitions_operator",
        ),
        sa.CheckConstraint(
            "min_value IS NULL OR max_value IS NULL OR min_value <= max_value",
            name="ck_preference_definitions_bounds",
        ),
        schema="app",
    )
    op.bulk_insert(definitions, DEFINITIONS)

    op.create_table(
        "store_preferences",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("store_id", sa.String(32), nullable=False),
        sa.Column("manager_id", sa.String(32), nullable=False),
        sa.Column("code", sa.String(48), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("value", JSONB(none_as_null=True), nullable=True),
        sa.Column("options", JSONB(none_as_null=True), nullable=True),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["code"],
            ["app.preference_definitions.code"],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'superseded', 'removed')",
            name="ck_store_preferences_status",
        ),
        sa.CheckConstraint(
            "options IS NULL OR jsonb_typeof(options) = 'object'",
            name="ck_store_preferences_options",
        ),
        schema="app",
    )
    op.create_index(
        "uq_store_preferences_active",
        "store_preferences",
        ["store_id", "manager_id", "code"],
        unique=True,
        schema="app",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "ix_store_preferences_manager_status",
        "store_preferences",
        ["store_id", "manager_id", "status"],
        schema="app",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_store_preferences_manager_status",
        table_name="store_preferences",
        schema="app",
    )
    op.drop_index(
        "uq_store_preferences_active",
        table_name="store_preferences",
        schema="app",
    )
    op.drop_table("store_preferences", schema="app")
    op.drop_table("preference_definitions", schema="app")
