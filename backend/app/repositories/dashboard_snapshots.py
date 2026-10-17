from datetime import datetime
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.dashboard_snapshot import DashboardSeriesSnapshot


class DashboardSnapshotRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def save(self, key: str, payload: Any, fetched_at: datetime) -> None:
        statement = insert(DashboardSeriesSnapshot).values(
            key=key, payload=payload, fetched_at=fetched_at
        )
        statement = statement.on_conflict_do_update(
            index_elements=["key"],
            set_={
                "payload": statement.excluded.payload,
                "fetched_at": statement.excluded.fetched_at,
            },
        )
        with Session(self.engine) as session, session.begin():
            session.execute(statement)

    def load(self, prefix: str) -> dict[str, tuple[Any, datetime]]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(
                    DashboardSeriesSnapshot.key,
                    DashboardSeriesSnapshot.payload,
                    DashboardSeriesSnapshot.fetched_at,
                ).where(DashboardSeriesSnapshot.key.startswith(prefix, autoescape=True))
            ).all()
        return {row[0]: (row[1], row[2]) for row in rows}
