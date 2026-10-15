import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("instruments.id"),
    )
    cik: Mapped[str] = mapped_column(String(10), nullable=False)
    filing_type: Mapped[str] = mapped_column(String(16), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(8), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    filed: Mapped[date] = mapped_column(Date, nullable=False)
    accession_no: Mapped[str] = mapped_column(String(24), nullable=False)
    source_url: Mapped[str] = mapped_column(String(300), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SecFact(Base):
    __tablename__ = "sec_facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    filing_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("filings.id", ondelete="CASCADE"),
        nullable=False,
    )
    concept: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 6), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    form: Mapped[str] = mapped_column(String(16), nullable=False)
    fiscal_year: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_period: Mapped[str] = mapped_column(String(8), nullable=False)
    frame: Mapped[str | None] = mapped_column(String(16))
    filed: Mapped[date] = mapped_column(Date, nullable=False)
    source_url: Mapped[str] = mapped_column(String(300), nullable=False)
