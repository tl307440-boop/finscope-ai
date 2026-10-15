"""首批行情与宏观表。行业外键留到产业表出现之后。

Revision ID: 20261015_0003
Revises: 20261015_0002
Create Date: 2026-10-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20261015_0003"
down_revision: str | None = "20261015_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

sync_job_status = postgresql.ENUM(
    "queued",
    "running",
    "succeeded",
    "partial",
    "failed",
    "cancelled",
    name="sync_job_status",
    create_type=False,
)
market_code = postgresql.ENUM("CN", "US", name="market_code", create_type=False)
instrument_type = postgresql.ENUM(
    "STOCK",
    "INDEX",
    "ETF",
    name="instrument_type",
    create_type=False,
)
bar_timeframe = postgresql.ENUM("1d", "1w", "1mo", name="bar_timeframe", create_type=False)
bar_adjustment = postgresql.ENUM("none", "qfq", "hfq", name="bar_adjustment", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    sync_job_status.create(bind, checkfirst=True)
    market_code.create(bind, checkfirst=True)
    instrument_type.create(bind, checkfirst=True)
    bar_timeframe.create(bind, checkfirst=True)
    bar_adjustment.create(bind, checkfirst=True)

    op.create_table(
        "data_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_public", postgresql.JSONB(), nullable=False),
        sa.Column("last_health_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_health_status", sa.String(length=32), nullable=True),
        sa.UniqueConstraint("code", name="uq_data_providers_code"),
    )
    op.create_table(
        "sync_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(length=64), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("scope", postgresql.JSONB(), nullable=False),
        sa.Column("dedupe_key", sa.String(length=200), nullable=False),
        sa.Column("status", sync_job_status, nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("rows_written", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("progress", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["data_providers.id"]),
        sa.ForeignKeyConstraint(["requested_by"], ["users.id"]),
        sa.UniqueConstraint("dedupe_key", name="uq_sync_jobs_dedupe_key"),
    )
    op.create_table(
        "sync_job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("meta", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["sync_jobs.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_sync_job_events_job_id", "sync_job_events", ["job_id"])
    op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("market", market_code, nullable=False),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=32), nullable=False),
        sa.Column("display_symbol", sa.String(length=32), nullable=False),
        sa.Column("name_cn", sa.String(length=128), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=False),
        sa.Column("type", instrument_type, nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("industry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("list_date", sa.Date(), nullable=True),
        sa.Column("delist_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("market", "exchange", "symbol", name="uq_instruments_identity"),
    )
    op.create_table(
        "trading_calendars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("exchange", sa.String(length=16), nullable=False),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("is_open", sa.Boolean(), nullable=False),
        sa.Column("session_json", postgresql.JSONB(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("exchange", "session_date", name="uq_trading_calendars_session"),
    )
    op.create_table(
        "market_bars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("timeframe", bar_timeframe, nullable=False),
        sa.Column("adjustment", bar_adjustment, nullable=False),
        sa.Column("open", sa.Numeric(20, 6), nullable=False),
        sa.Column("high", sa.Numeric(20, 6), nullable=False),
        sa.Column("low", sa.Numeric(20, 6), nullable=False),
        sa.Column("close", sa.Numeric(20, 6), nullable=False),
        sa.Column("volume", sa.Numeric(24, 6), nullable=False),
        sa.Column("amount", sa.Numeric(24, 6), nullable=False),
        sa.Column("turnover_rate", sa.Numeric(12, 6), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_flag", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.ForeignKeyConstraint(["provider_id"], ["data_providers.id"]),
        sa.UniqueConstraint(
            "instrument_id",
            "trade_date",
            "timeframe",
            "adjustment",
            "provider_id",
            name="uq_market_bars_identity",
        ),
    )
    op.create_table(
        "macro_series",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("series_code", sa.String(length=64), nullable=False),
        sa.Column("name_cn", sa.String(length=128), nullable=False),
        sa.Column("name_en", sa.String(length=128), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("base_unit", sa.String(length=32), nullable=False),
        sa.Column("seasonal_adjustment", sa.String(length=32), nullable=False),
        sa.Column("transform_rule", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["data_providers.id"]),
        sa.UniqueConstraint("series_code", name="uq_macro_series_code"),
    )
    op.create_table(
        "macro_observations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("series_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("observation_date", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(24, 6), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("realtime_start", sa.Date(), nullable=True),
        sa.Column("realtime_end", sa.Date(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_record_key", sa.String(length=128), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["series_id"], ["macro_series.id"]),
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_macro_observations_identity
        ON macro_observations (
            series_id,
            observation_date,
            COALESCE(realtime_start, DATE '0001-01-01'),
            revision_no
        )
        """
    )


def downgrade() -> None:
    op.drop_table("macro_observations")
    op.drop_table("macro_series")
    op.drop_table("market_bars")
    op.drop_table("trading_calendars")
    op.drop_table("instruments")
    op.drop_index("ix_sync_job_events_job_id", table_name="sync_job_events")
    op.drop_table("sync_job_events")
    op.drop_table("sync_jobs")
    op.drop_table("data_providers")
    bind = op.get_bind()
    bar_adjustment.drop(bind, checkfirst=True)
    bar_timeframe.drop(bind, checkfirst=True)
    instrument_type.drop(bind, checkfirst=True)
    market_code.drop(bind, checkfirst=True)
    sync_job_status.drop(bind, checkfirst=True)
