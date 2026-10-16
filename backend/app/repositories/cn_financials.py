import uuid
from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models.sec import CnStatementFact
from app.providers.eastmoney_statements import CnFact
from app.repositories.filings import FactRecord
from app.utils.time import utc_now

CN_FILING_STATUS = "eastmoney"


class CnFinancialRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert_facts(self, instrument_id: UUID, facts: tuple[CnFact, ...] | list[CnFact]) -> int:
        """幂等写入；上游修订了数值就更新，返回新增或被修订的行数。"""

        if not facts:
            return 0
        fetched_at = utc_now()
        changed = 0
        with Session(self.engine) as session, session.begin():
            for fact in facts:
                statement = pg_insert(CnStatementFact).values(
                    id=uuid.uuid4(),
                    instrument_id=instrument_id,
                    concept=fact.concept,
                    unit=fact.unit,
                    value=fact.value,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                    form=fact.form,
                    fiscal_year=fact.fiscal_year,
                    fiscal_period=fact.fiscal_period,
                    filed=fact.filed,
                    source_field=fact.source_field,
                    source_url=fact.source_url,
                    fetched_at=fetched_at,
                )
                statement = statement.on_conflict_do_update(
                    constraint="uq_cn_statement_facts_identity",
                    set_={
                        "value": statement.excluded.value,
                        "filed": statement.excluded.filed,
                        "form": statement.excluded.form,
                        "source_field": statement.excluded.source_field,
                        "fetched_at": statement.excluded.fetched_at,
                    },
                    where=(CnStatementFact.value != statement.excluded.value)
                    | (CnStatementFact.filed != statement.excluded.filed),
                )
                # 不用 rowcount：psycopg 对 upsert 可能返回 -1；RETURNING 只返回真正写入的行。
                changed += len(session.execute(statement.returning(CnStatementFact.id)).all())
        return changed

    def list_facts(self, instrument_id: UUID, as_of: date) -> list[FactRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(CnStatementFact)
                .where(
                    CnStatementFact.instrument_id == instrument_id,
                    CnStatementFact.filed <= as_of,
                )
                .order_by(CnStatementFact.concept, CnStatementFact.period_end)
            ).all()
            return [
                FactRecord(
                    id=row.id,
                    concept=row.concept,
                    unit=row.unit,
                    value=row.value,
                    period_start=row.period_start,
                    period_end=row.period_end,
                    form=row.form,
                    fiscal_year=row.fiscal_year,
                    fiscal_period=row.fiscal_period,
                    frame=None,
                    filed=row.filed,
                    accession_no=f"CN-{row.period_end:%Y%m%d}",
                    source_url=row.source_url,
                    filing_status=CN_FILING_STATUS,
                )
                for row in rows
            ]

    def last_fetched_at(self, instrument_id: UUID) -> datetime | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(func.max(CnStatementFact.fetched_at)).where(
                    CnStatementFact.instrument_id == instrument_id
                )
            )

    def latest_period_end(self, instrument_id: UUID) -> date | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(func.max(CnStatementFact.period_end)).where(
                    CnStatementFact.instrument_id == instrument_id
                )
            )
