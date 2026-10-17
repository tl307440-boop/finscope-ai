import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.dashboard_metric import DashboardDailyMetric


@dataclass(frozen=True)
class StoredMetric:
    trade_date: date
    value: Decimal


class DashboardMetricRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def record(self, trade_date: date, values: dict[str, Decimal], computed_at: datetime) -> None:
        if not values:
            return
        statement = insert(DashboardDailyMetric).values(
            [
                {
                    "id": uuid.uuid4(),
                    "trade_date": trade_date,
                    "metric": metric,
                    "value": value,
                    "computed_at": computed_at,
                }
                for metric, value in values.items()
            ]
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_dashboard_daily_metrics_identity",
            set_={
                "value": statement.excluded["value"],
                "computed_at": statement.excluded["computed_at"],
            },
        )
        with Session(self.engine) as session, session.begin():
            session.execute(statement)

    def previous(self, metrics: list[str], before: date) -> dict[str, StoredMetric]:
        """每个指标在 before 之前最近一个交易日的值。"""

        found: dict[str, StoredMetric] = {}
        with Session(self.engine) as session:
            for metric in metrics:
                row = session.execute(
                    select(DashboardDailyMetric.trade_date, DashboardDailyMetric.value)
                    .where(
                        DashboardDailyMetric.metric == metric,
                        DashboardDailyMetric.trade_date < before,
                    )
                    .order_by(DashboardDailyMetric.trade_date.desc())
                    .limit(1)
                ).first()
                if row is not None:
                    found[metric] = StoredMetric(trade_date=row[0], value=row[1])
        return found
