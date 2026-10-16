from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app.api.deps import get_current_user, require_admin
from app.api.responses import success_response
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.repositories.macro_china import MacroChinaRepository
from app.repositories.news_events import NewsEventRecord, NewsEventRepository
from app.repositories.reports import ReportRepository
from app.services.auth_types import UserRecord
from app.services.china_macro import CHINA_SERIES, public_status
from app.services.daily_digest import build_digest
from app.services.disclosure_events import (
    EXTERNAL_FEED_NAMES,
    STREAM_LABEL,
    DisclosureDraft,
    NewsEventInput,
    group_by_day,
    group_by_type,
    heat_label,
)
from app.utils.time import utc_now

router = APIRouter(tags=["研究板"])

CHINA_CSV_NOTICE = (
    "还没有导入正式 CSV：占位序列全部按 unavailable 返回，不填任何编造的数字。"
    "管理员可以用 POST /api/v1/macro/china/observations 导入国家统计局（或同源口径）的 CSV。"
)

# 页面默认看两周的公告窗口。
DEFAULT_HOTSPOT_DAYS = 14

NEWS_DISABLED_NOTICE = (
    "合规公告数据源没有开启（ENABLE_NEWS=false），本次不读取任何新闻事件，"
    "返回空列表而不是编造热度。开启并导入公告后这里才会有真实计数。"
)


def _day_start(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=UTC)


def _day_end(value: date) -> datetime:
    return datetime.combine(value, time.max, tzinfo=UTC)


def _as_utc(moment: datetime) -> datetime:
    """库里一律存 UTC。请求里没带时区的按 UTC 解释，不做本地时区猜测。"""

    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


class ChinaSeriesItem(BaseModel):
    code: str
    name: str
    unit: str
    status: str
    observation_count: int
    latest_value: str | None = None
    latest_observation_date: date | None = None
    source: str | None = None
    published_at: datetime | None = None


class ChinaSeriesList(BaseModel):
    series: list[ChinaSeriesItem]
    notice: str


class HotspotTopicItem(BaseModel):
    event_type: str
    label: str
    count: int


class HotspotTimelineItem(BaseModel):
    day: str
    count: int
    titles: list[str]


class HotspotEventItem(BaseModel):
    id: UUID
    title: str
    canonical_url: str
    source_name: str
    source_badge: str
    event_type: str
    event_type_label: str
    published_at: datetime
    usage_rights: str
    quality_flag: str
    industry_id: UUID | None = None


class HotspotData(BaseModel):
    label: str
    count: int
    start: date
    end: date
    industry_id: UUID | None = None
    stream: str
    topics: list[HotspotTopicItem]
    timeline: list[HotspotTimelineItem]
    events: list[HotspotEventItem]
    sources: list[str]
    pending_feeds: list[str]
    notice: str


class NewsEventCreate(BaseModel):
    """合规公告/新闻事件。source_name 必须在允许名单里。"""

    title: str = Field(min_length=1, max_length=200)
    canonical_url: str = Field(min_length=1, max_length=300)
    source_name: str = Field(min_length=1, max_length=64)
    published_at: datetime
    event_type: str = Field(min_length=1, max_length=32)
    usage_rights: str = Field(min_length=1, max_length=120)


class NewsEventImport(BaseModel):
    events: list[NewsEventCreate] = Field(min_length=1, max_length=200)
    industry_id: UUID | None = None


class NewsEventImportResult(BaseModel):
    accepted: int
    duplicate_count: int
    errors: list[str]
    events: list[HotspotEventItem]
    notice: str


class DigestLine(BaseModel):
    title: str
    summary: str


class DigestData(BaseModel):
    items: list[DigestLine]


def _engine(request: Request) -> Engine:
    return cast(Engine, request.app.state.engine)


def _event_item(record: NewsEventRecord) -> HotspotEventItem:
    return HotspotEventItem(
        id=record.id,
        title=record.title,
        canonical_url=record.canonical_url,
        source_name=record.source_name,
        source_badge=record.source_label,
        event_type=record.event_type,
        event_type_label=record.event_type_label,
        published_at=record.published_at,
        usage_rights=record.usage_rights,
        quality_flag=record.quality_flag,
        industry_id=record.industry_id,
    )


@router.post("/market/hotspots/events", summary="导入合规公告事件（仅管理员）")
def import_hotspot_events(
    body: NewsEventImport,
    request: Request,
    admin: Annotated[UserRecord, Depends(require_admin)],
) -> JSONResponse:
    """合规来源公告的正式入口。不合规的逐条回错误，不落库。"""

    del admin
    repository = NewsEventRepository(_engine(request))
    if body.industry_id is not None and not repository.industry_exists(body.industry_id):
        raise AppError(code="NOT_FOUND", message="找不到这个行业", status_code=404)
    items = [
        NewsEventInput(
            index=index,
            draft=DisclosureDraft(
                title=item.title,
                canonical_url=item.canonical_url,
                source_name=item.source_name,
                published_at=_as_utc(item.published_at),
                event_type=item.event_type,
            ),
            usage_rights=item.usage_rights,
            industry_id=None if body.industry_id is None else str(body.industry_id),
        )
        for index, item in enumerate(body.events, start=1)
    ]
    outcome = repository.import_events(items, industry_id=body.industry_id)
    created = repository.get_many(outcome.created_ids)
    payload = NewsEventImportResult(
        accepted=outcome.accepted,
        duplicate_count=len(outcome.duplicates),
        errors=[*outcome.rejected, *outcome.duplicates],
        events=[_event_item(record) for record in created],
        notice=(
            "accepted 只统计本次真正落库的公告；重复与不合规的条目在 errors 里逐条说明。"
            "空列表就是没有数据，不会用占位热度顶替。"
        ),
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/market/hotspots", summary="合规公告热度")
def read_hotspots(
    request: Request,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
    industry_id: Annotated[UUID | None, Query()] = None,
) -> JSONResponse:
    """按公布时间区间聚合公告事件。没有合规数据源时如实返回空列表。"""

    today = utc_now().date()
    window_end = today if end is None else end
    window_start = window_end - timedelta(days=DEFAULT_HOTSPOT_DAYS - 1) if start is None else start
    if window_start > window_end:
        raise AppError(code="INVALID_RANGE", message="start 不能晚于 end", status_code=422)
    settings = get_settings()
    repository = NewsEventRepository(_engine(request))
    if industry_id is not None and not repository.industry_exists(industry_id):
        raise AppError(code="NOT_FOUND", message="找不到这个行业", status_code=404)
    events: list[NewsEventRecord] = []
    notice = ""
    if settings.enable_news:
        events = repository.list_between(
            start=_day_start(window_start),
            end=_day_end(window_end),
            industry_id=industry_id,
        )
    else:
        notice = NEWS_DISABLED_NOTICE
    timeline = group_by_day([(item.published_at, item.title) for item in events])
    topics = group_by_type([(item.event_type, item.title) for item in events])
    if not notice:
        span = f"{window_start.isoformat()} 至 {window_end.isoformat()}"
        notice = f"{span} 收录 {len(events)} 条合规公告。"
    payload = HotspotData(
        label=heat_label(len(events)),
        count=len(events),
        start=window_start,
        end=window_end,
        industry_id=industry_id,
        stream=STREAM_LABEL,
        topics=[
            HotspotTopicItem(event_type=item.event_type, label=item.label, count=item.count)
            for item in topics
        ],
        timeline=[
            HotspotTimelineItem(day=item.day, count=item.count, titles=list(item.titles))
            for item in timeline
        ],
        events=[_event_item(record) for record in events],
        sources=sorted({item.source_label for item in events}),
        pending_feeds=[label for _name, label in EXTERNAL_FEED_NAMES],
        notice=notice,
    )
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/macro/china", summary="中国宏观序列状态")
def read_china_macro(request: Request) -> JSONResponse:
    """每条序列的真实落库情况：条数、最新值、来源、公布时间。"""

    today = utc_now().date()
    summaries = {
        item.series_code: item
        for item in MacroChinaRepository(_engine(request)).list_series_summary()
    }
    items: list[ChinaSeriesItem] = []
    for code, name, unit in CHINA_SERIES:
        summary = summaries.get(code)
        stored = summary.observation_count if summary is not None else 0
        latest = summary.latest if summary is not None else None
        items.append(
            ChinaSeriesItem(
                code=code,
                name=name,
                unit=unit,
                status=public_status(
                    code,
                    observation_count=stored,
                    latest_observation_date=None if latest is None else latest.observation_date,
                    today=today,
                ),
                observation_count=stored,
                latest_value=None if latest is None else str(latest.value),
                latest_observation_date=None if latest is None else latest.observation_date,
                source=None if latest is None else latest.source,
                published_at=None if latest is None else latest.published_at,
            )
        )
    payload = ChinaSeriesList(series=items, notice=CHINA_CSV_NOTICE)
    return JSONResponse(status_code=200, content=success_response(request, payload))


@router.get("/me/digest", summary="站内每日摘要")
def read_digest(
    request: Request,
    user: Annotated[UserRecord, Depends(get_current_user)],
) -> JSONResponse:
    """站内摘要：最近保存的报告标题 + 近 24 小时收录的合规公告条数。"""

    engine = _engine(request)
    titles = [item.title for item in ReportRepository(engine).list_owned(user.id, "") if item.title]
    now = utc_now()
    day_start = datetime.combine(now.date(), time.min, tzinfo=UTC)
    announcement_count = 0
    if get_settings().enable_news:
        announcement_count = len(NewsEventRepository(engine).list_between(start=day_start, end=now))
    items = build_digest(titles, announcement_count)
    return JSONResponse(
        status_code=200,
        content=success_response(
            request,
            DigestData(
                items=[DigestLine(title=item.title, summary=item.summary) for item in items]
            ),
        ),
    )
