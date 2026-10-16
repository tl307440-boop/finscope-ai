import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.financial import FinancialMetric
from app.services.financial_metrics import METHOD_VERSION


@dataclass(frozen=True)
class MetricRow:
    metric_code: str
    period_type: str
    period_end: date
    value: Decimal | None
    unit: str
    quality_flag: str
    source_facts: dict[str, object]
    published_at: datetime | None


class FinancialMetricRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(
        self,
        *,
        instrument_id: UUID,
        metric_code: str,
        period_type: str,
        period_end: date,
        value: Decimal | None,
        unit: str,
        quality_flag: str,
        source_facts: dict[str, object],
        published_at: datetime | None,
    ) -> UUID:
        with Session(self.engine) as session, session.begin():
            existing = session.scalars(
                select(FinancialMetric).where(
                    FinancialMetric.instrument_id == instrument_id,
                    FinancialMetric.metric_code == metric_code,
                    FinancialMetric.period_type == period_type,
                    FinancialMetric.period_end == period_end,
                    FinancialMetric.metric_version == METHOD_VERSION,
                )
            ).one_or_none()
            if existing is None:
                row = FinancialMetric(
                    id=uuid.uuid4(),
                    instrument_id=instrument_id,
                    metric_code=metric_code,
                    period_type=period_type,
                    period_end=period_end,
                    value=value,
                    unit=unit,
                    calculation_method="derived",
                    source_facts=source_facts,
                    metric_version=METHOD_VERSION,
                    published_at=published_at,
                    quality_flag=quality_flag,
                )
                session.add(row)
                return row.id
            existing.value = value
            existing.quality_flag = quality_flag
            existing.source_facts = source_facts
            return existing.id

    def read_value(
        self,
        *,
        instrument_id: UUID,
        metric_code: str,
        period_type: str,
        period_end: date,
    ) -> Decimal | None:
        with Session(self.engine) as session:
            row = session.scalars(
                select(FinancialMetric).where(
                    FinancialMetric.instrument_id == instrument_id,
                    FinancialMetric.metric_code == metric_code,
                    FinancialMetric.period_type == period_type,
                    FinancialMetric.period_end == period_end,
                    FinancialMetric.metric_version == METHOD_VERSION,
                )
            ).one_or_none()
            if row is None:
                return None
            return row.value

    def latest_for_instrument(self, instrument_id: UUID, as_of: date) -> list[MetricRow]:
        """每个指标取最新一期（必须有发布时间且不晚于观察日才可见）。"""

        with Session(self.engine) as session:
            rows = session.scalars(
                select(FinancialMetric)
                .where(
                    FinancialMetric.instrument_id == instrument_id,
                    FinancialMetric.metric_version == METHOD_VERSION,
                )
                .order_by(FinancialMetric.period_end.desc())
            ).all()
            latest: dict[str, FinancialMetric] = {}
            for row in rows:
                if row.published_at is None or row.published_at.date() > as_of:
                    continue
                latest.setdefault(row.metric_code, row)
            return [
                MetricRow(
                    metric_code=row.metric_code,
                    period_type=row.period_type,
                    period_end=row.period_end,
                    value=row.value,
                    unit=row.unit,
                    quality_flag=row.quality_flag,
                    source_facts=row.source_facts,
                    published_at=row.published_at,
                )
                for row in latest.values()
            ]
