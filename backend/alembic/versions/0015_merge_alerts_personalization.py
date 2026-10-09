"""Join alerts and personalization without changing already-applied revisions."""

revision = "0015_merge_personalization"
down_revision = ("0014_alert_dismissals", "0013_personalization")


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
