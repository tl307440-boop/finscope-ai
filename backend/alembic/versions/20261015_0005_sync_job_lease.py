"""sync job lease and active dedupe

Revision ID: 20261015_0005
Revises: 20261015_0004
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0005"
down_revision: str | None = "20261015_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sync_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.drop_constraint("uq_sync_jobs_dedupe_key", "sync_jobs", type_="unique")
    op.create_index(
        "uq_sync_jobs_active_dedupe",
        "sync_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('queued'::sync_job_status, 'running'::sync_job_status)"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_sync_jobs_active_dedupe", table_name="sync_jobs")
    op.create_unique_constraint("uq_sync_jobs_dedupe_key", "sync_jobs", ["dedupe_key"])
    op.drop_column("sync_jobs", "lease_expires_at")
