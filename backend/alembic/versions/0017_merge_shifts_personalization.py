"""Join shift handovers and personalization progress into one migration head."""

revision = "0017_merge_personalization"
down_revision = ("0016_shifts", "0016_personalization_progress")


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
