import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.db.models.industry import Industry
from app.db.models.news import NewsEvent
from app.services.disclosure_events import (
    NewsEventInput,
    event_hash,
    event_type_label,
    source_label,
    validate_import,
)
from app.utils.time import utc_now

QUALITY_VERIFIED = "verified"


@dataclass(frozen=True)
class ImportOutcome:
    """accepted 是本次真正落库的条数；duplicates/rejected 逐条说明原因。"""

    accepted: int
    created_ids: tuple[UUID, ...]
    duplicates: list[str]
    rejected: list[str]


@dataclass(frozen=True)
class NewsEventRecord:
    """已经落库的公告事件。"""

    id: UUID
    title: str
    canonical_url: str
    source_name: str
    source_label: str
    event_type: str
    event_type_label: str
    published_at: datetime
    captured_at: datetime
    usage_rights: str
    quality_flag: str
    industry_id: UUID | None


class NewsEventRepository:
    """合规公告事件的写入（按 dedupe_hash 幂等）与按日期区间查询。"""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def import_events(
        self,
        items: Sequence[NewsEventInput],
        *,
        captured_at: datetime | None = None,
        industry_id: UUID | None = None,
    ) -> ImportOutcome:
        """逐条校验后再落库。不合规的只回错误，不写库。"""

        moment = utc_now() if captured_at is None else captured_at
        rejected: list[str] = []
        duplicates: list[str] = []
        prepared: list[tuple[int, NewsEventInput, str]] = []
        for item in items:
            index = item.index
            problem = validate_import(item)
            if problem is not None:
                rejected.append(f"第 {index} 条：{problem}")
                continue
            digest = event_hash(item.draft.canonical_url, item.draft.title, item.draft.published_at)
            prepared.append((index, item, digest))
        if not prepared:
            return ImportOutcome(accepted=0, created_ids=(), duplicates=[], rejected=rejected)
        written: list[UUID] = []
        with Session(self.engine) as session, session.begin():
            existing = set(
                session.scalars(
                    select(NewsEvent.dedupe_hash).where(
                        NewsEvent.dedupe_hash.in_([digest for _index, _item, digest in prepared])
                    )
                ).all()
            )
            same_batch: set[str] = set()
            for index, item, digest in prepared:
                if digest in existing or digest in same_batch:
                    duplicates.append(f"第 {index} 条：同一条公告已经收录过")
                    continue
                same_batch.add(digest)
                event_id = uuid.uuid4()
                session.add(
                    NewsEvent(
                        id=event_id,
                        title=item.draft.title.strip(),
                        canonical_url=item.draft.canonical_url.strip(),
                        source_name=item.draft.source_name.strip(),
                        published_at=item.draft.published_at,
                        captured_at=moment,
                        event_type=item.draft.event_type.strip(),
                        dedupe_hash=digest,
                        usage_rights=item.usage_rights.strip(),
                        quality_flag=QUALITY_VERIFIED,
                        industry_id=industry_id,
                    )
                )
                written.append(event_id)
        return ImportOutcome(
            accepted=len(written),
            created_ids=tuple(written),
            duplicates=duplicates,
            rejected=rejected,
        )

    def list_between(
        self,
        *,
        start: datetime,
        end: datetime,
        industry_id: UUID | None = None,
    ) -> list[NewsEventRecord]:
        """按公布时间区间查询；industry_id 给出时只返回该行业的事件。"""

        with Session(self.engine) as session:
            statement = select(NewsEvent).where(
                NewsEvent.published_at >= start,
                NewsEvent.published_at <= end,
            )
            if industry_id is not None:
                statement = statement.where(NewsEvent.industry_id == industry_id)
            rows = session.scalars(
                statement.order_by(NewsEvent.published_at.desc(), NewsEvent.title)
            ).all()
            return [_record(row) for row in rows]

    def industry_exists(self, industry_id: UUID) -> bool:
        with Session(self.engine) as session:
            return session.get(Industry, industry_id) is not None

    def get_many(self, event_ids: Sequence[UUID]) -> list[NewsEventRecord]:
        """按 id 取回刚写入的事件，保持传入顺序。"""

        if not event_ids:
            return []
        with Session(self.engine) as session:
            rows = session.scalars(select(NewsEvent).where(NewsEvent.id.in_(list(event_ids)))).all()
            by_id = {row.id: row for row in rows}
            return [_record(by_id[event_id]) for event_id in event_ids if event_id in by_id]

    def delete_by_id(self, event_id: UUID) -> bool:
        """清理用：删掉一条已经收录的事件，返回是否真的删到了。"""

        with Session(self.engine) as session, session.begin():
            row = session.get(NewsEvent, event_id)
            if row is None:
                return False
            session.delete(row)
            return True


def _record(row: NewsEvent) -> NewsEventRecord:
    return NewsEventRecord(
        id=row.id,
        title=row.title,
        canonical_url=row.canonical_url,
        source_name=row.source_name,
        source_label=source_label(row.source_name),
        event_type=row.event_type,
        event_type_label=event_type_label(row.event_type),
        published_at=row.published_at,
        captured_at=row.captured_at,
        usage_rights=row.usage_rights,
        quality_flag=row.quality_flag,
        industry_id=row.industry_id,
    )
