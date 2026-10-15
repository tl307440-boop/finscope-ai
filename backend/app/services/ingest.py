from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.providers.errors import ProviderError
from app.providers.quality import CheckedBar, RawBar, accept_bar


class BarWriter(Protocol):
    def upsert(self, bars: list[CheckedBar], *, instrument_id: UUID, provider_id: UUID) -> int: ...


@dataclass(frozen=True)
class IngestOutcome:
    rows_written: int
    rows_rejected: int
    rows_for_review: int
    error: ProviderError | None = None

    @property
    def preserved_existing(self) -> bool:
        return self.error is not None or self.rows_written == 0


def ingest_bars(
    raw_bars: list[RawBar] | ProviderError,
    writer: BarWriter,
    *,
    instrument_id: UUID,
    provider_id: UUID,
) -> IngestOutcome:
    """上游异常时不调用写入。被拒绝的 K 线也不会入库。"""
    if isinstance(raw_bars, ProviderError):
        return IngestOutcome(rows_written=0, rows_rejected=0, rows_for_review=0, error=raw_bars)
    accepted: list[CheckedBar] = []
    rejected = 0
    review = 0
    for raw in raw_bars:
        checked = accept_bar(raw)
        if checked is None:
            rejected += 1
            continue
        if checked.quality_flag == "review":
            review += 1
        accepted.append(checked)
    written = writer.upsert(accepted, instrument_id=instrument_id, provider_id=provider_id)
    return IngestOutcome(rows_written=written, rows_rejected=rejected, rows_for_review=review)
