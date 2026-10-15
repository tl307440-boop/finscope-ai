import uuid
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.financial import FinancialMetric
from app.services.financial_metrics import METHOD_VERSION


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
