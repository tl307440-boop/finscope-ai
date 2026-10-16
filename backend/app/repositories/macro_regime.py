import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Engine, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.db.models.macro_regime import MacroRegimeSnapshot


@dataclass(frozen=True)
class RegimeSnapshot:
    as_of: date
    model_version: str
    rule_version: str
    regime_code: str
    payload: dict[str, object]
    computed_at: datetime


class MacroRegimeRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert(self, snapshot: RegimeSnapshot) -> None:
        values = {
            "id": uuid.uuid4(),
            "as_of": snapshot.as_of,
            "model_version": snapshot.model_version,
            "rule_version": snapshot.rule_version,
            "regime_code": snapshot.regime_code,
            "payload": snapshot.payload,
            "computed_at": snapshot.computed_at,
        }
        statement = insert(MacroRegimeSnapshot).values(**values)
        statement = statement.on_conflict_do_update(
            constraint="uq_macro_regime_snapshots_identity",
            set_={
                "rule_version": statement.excluded.rule_version,
                "regime_code": statement.excluded.regime_code,
                "payload": statement.excluded.payload,
                "computed_at": statement.excluded.computed_at,
            },
        )
        with Session(self.engine) as session, session.begin():
            session.execute(statement)

    def history(self, limit: int) -> list[RegimeSnapshot]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(MacroRegimeSnapshot)
                .order_by(MacroRegimeSnapshot.as_of.desc(), MacroRegimeSnapshot.computed_at.desc())
                .limit(limit)
            ).all()
            return [
                RegimeSnapshot(
                    as_of=row.as_of,
                    model_version=row.model_version,
                    rule_version=row.rule_version,
                    regime_code=row.regime_code,
                    payload=row.payload,
                    computed_at=row.computed_at,
                )
                for row in rows
            ]
