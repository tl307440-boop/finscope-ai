from datetime import date
from uuid import UUID

from pydantic import BaseModel

from app.repositories.filings import FactRecord, FilingRecord
from app.repositories.macro import decimal_text

FIXTURE_NOTICE = "本地样例，不是本次从 SEC 拉取。"


class FilingItem(BaseModel):
    id: UUID
    cik: str
    filing_type: str
    accession_no: str
    filed: date
    fiscal_year: int
    fiscal_period: str
    source_url: str
    status: str


class FilingList(BaseModel):
    cik: str
    notice: str
    filings: list[FilingItem]


class FactItem(BaseModel):
    concept: str
    unit: str
    value: str
    period_start: date | None = None
    period_end: date
    form: str
    fiscal_year: int
    fiscal_period: str
    frame: str | None = None
    filed: date
    accession_no: str
    source_url: str


class FactList(BaseModel):
    cik: str
    as_of: date
    notice: str
    facts: list[FactItem]


def build_filings(cik: str, rows: list[FilingRecord]) -> FilingList:
    return FilingList(
        cik=cik,
        notice=FIXTURE_NOTICE,
        filings=[
            FilingItem(
                id=row.id,
                cik=row.cik,
                filing_type=row.filing_type,
                accession_no=row.accession_no,
                filed=row.filed,
                fiscal_year=row.fiscal_year,
                fiscal_period=row.fiscal_period,
                source_url=row.source_url,
                status=row.status,
            )
            for row in rows
        ],
    )


def build_facts(cik: str, as_of: date, rows: list[FactRecord]) -> FactList:
    return FactList(
        cik=cik,
        as_of=as_of,
        notice=FIXTURE_NOTICE,
        facts=[
            FactItem(
                concept=row.concept,
                unit=row.unit,
                value=decimal_text(row.value),
                period_start=row.period_start,
                period_end=row.period_end,
                form=row.form,
                fiscal_year=row.fiscal_year,
                fiscal_period=row.fiscal_period,
                frame=row.frame,
                filed=row.filed,
                accession_no=row.accession_no,
                source_url=row.source_url,
            )
            for row in rows
        ],
    )
