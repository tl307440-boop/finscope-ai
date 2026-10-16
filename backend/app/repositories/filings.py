import uuid
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from app.db.models.market import Instrument
from app.db.models.sec import Filing, SecFact
from app.providers.sec_edgar import ParsedFact
from app.utils.time import utc_now


@dataclass(frozen=True)
class FilingRecord:
    id: UUID
    cik: str
    filing_type: str
    accession_no: str
    filed: date
    fiscal_year: int
    fiscal_period: str
    source_url: str
    status: str
    instrument_id: UUID | None


@dataclass(frozen=True)
class FactRecord:
    id: UUID
    concept: str
    unit: str
    value: Decimal
    period_start: date | None
    period_end: date
    form: str
    fiscal_year: int
    fiscal_period: str
    frame: str | None
    filed: date
    accession_no: str
    source_url: str
    filing_status: str = "fixture"


class FilingRepository:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def upsert_facts(
        self,
        facts: tuple[ParsedFact, ...] | list[ParsedFact],
        sha256: str,
        *,
        status: str = "fixture",
    ) -> int:
        if not facts:
            return 0
        written = 0
        now = utc_now()
        with Session(self.engine) as session, session.begin():
            instrument_id = _instrument_id(session, facts[0].cik)
            filings: dict[str, Filing] = {}
            for fact in facts:
                filing = filings.get(fact.accession_no)
                if filing is None:
                    filing = _filing(session, fact, sha256, instrument_id, now, status)
                    filings[fact.accession_no] = filing
                if _fact_exists(session, filing.id, fact):
                    continue
                session.add(_fact_row(filing.id, fact))
                written += 1
        return written

    def list_filings(self, cik: str) -> list[FilingRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(Filing).where(Filing.cik == cik).order_by(Filing.filed, Filing.accession_no)
            ).all()
            return [_filing_record(row) for row in rows]

    def list_facts(self, cik: str, as_of: date) -> list[FactRecord]:
        with Session(self.engine) as session:
            rows = session.scalars(
                select(SecFact)
                .join(Filing, Filing.id == SecFact.filing_id)
                .where(Filing.cik == cik, SecFact.filed <= as_of)
                .order_by(SecFact.concept, SecFact.unit, SecFact.filed)
            ).all()
            chosen: dict[tuple[str, str, date, date | None], SecFact] = {}
            for row in rows:
                key = (row.concept, row.unit, row.period_end, row.period_start)
                current = chosen.get(key)
                if current is None or row.filed > current.filed:
                    chosen[key] = row
            records: list[FactRecord] = []
            for row in chosen.values():
                filing = session.get(Filing, row.filing_id)
                accession = filing.accession_no if filing is not None else ""
                status = filing.status if filing is not None else "fixture"
                records.append(_fact_record(row, accession, status))
            return records

    def latest_edgar_period_end(self, cik: str) -> date | None:
        with Session(self.engine) as session:
            return session.scalar(
                select(func.max(Filing.period_end)).where(
                    Filing.cik == cik, Filing.status == "edgar"
                )
            )

    def last_edgar_recorded_at(self, cik: str) -> datetime | None:
        """最近一次真实入库一份 EDGAR 申报的时间，作为「从没走过任务队列」时的同步时间下限。"""

        with Session(self.engine) as session:
            return session.scalar(
                select(func.max(Filing.created_at)).where(
                    Filing.cik == cik, Filing.status == "edgar"
                )
            )

    def has_live_filings(self, cik: str) -> bool:
        with Session(self.engine) as session:
            row = session.scalars(
                select(Filing.id).where(Filing.cik == cik, Filing.status == "edgar").limit(1)
            ).first()
            return row is not None


def _instrument_id(session: Session, cik: str) -> UUID | None:
    row = session.scalars(select(Instrument).where(Instrument.cik == cik)).first()
    if row is None:
        return None
    return row.id


def _filing(
    session: Session,
    fact: ParsedFact,
    sha256: str,
    instrument_id: UUID | None,
    now: datetime,
    status: str,
) -> Filing:
    existing = session.scalars(
        select(Filing).where(Filing.accession_no == fact.accession_no)
    ).one_or_none()
    if existing is not None:
        if status == "edgar" and existing.status != "edgar":
            existing.status = "edgar"
            existing.sha256 = sha256
        return existing
    filing = Filing(
        id=uuid.uuid4(),
        instrument_id=instrument_id,
        cik=fact.cik,
        filing_type=fact.form,
        fiscal_year=fact.fiscal_year,
        fiscal_period=fact.fiscal_period,
        period_start=fact.period_start,
        period_end=fact.period_end,
        filed=fact.filed,
        accession_no=fact.accession_no,
        source_url=fact.source_url,
        sha256=sha256,
        currency=fact.unit,
        status=status,
        created_at=now,
    )
    session.add(filing)
    session.flush()
    return filing


def _fact_exists(session: Session, filing_id: UUID, fact: ParsedFact) -> bool:
    rows = session.scalars(
        select(SecFact).where(
            SecFact.filing_id == filing_id,
            SecFact.concept == fact.concept,
            SecFact.unit == fact.unit,
            SecFact.period_end == fact.period_end,
        )
    ).all()
    return any(row.period_start == fact.period_start for row in rows)


def _fact_row(filing_id: UUID, fact: ParsedFact) -> SecFact:
    return SecFact(
        id=uuid.uuid4(),
        filing_id=filing_id,
        concept=fact.concept,
        unit=fact.unit,
        value=fact.value,
        period_start=fact.period_start,
        period_end=fact.period_end,
        form=fact.form,
        fiscal_year=fact.fiscal_year,
        fiscal_period=fact.fiscal_period,
        frame=fact.frame,
        filed=fact.filed,
        source_url=fact.source_url,
    )


def _filing_record(row: Filing) -> FilingRecord:
    return FilingRecord(
        id=row.id,
        cik=row.cik,
        filing_type=row.filing_type,
        accession_no=row.accession_no,
        filed=row.filed,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        source_url=row.source_url,
        status=row.status,
        instrument_id=row.instrument_id,
    )


def _fact_record(row: SecFact, accession_no: str, filing_status: str) -> FactRecord:
    return FactRecord(
        id=row.id,
        concept=row.concept,
        unit=row.unit,
        value=row.value,
        period_start=row.period_start,
        period_end=row.period_end,
        form=row.form,
        fiscal_year=row.fiscal_year,
        fiscal_period=row.fiscal_period,
        frame=row.frame,
        filed=row.filed,
        accession_no=accession_no,
        source_url=row.source_url,
        filing_status=filing_status,
    )
