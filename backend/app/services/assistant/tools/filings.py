"""`filing_fetch`：问到某份公告时现抓正文（知识库设计 §5.1）。

A 股走巨潮，美股走 EDGAR 的 8-K（优先 Exhibit 99.1）。本回合最多花 20 秒；
来不及就交给 worker 继续，回答里给出标题和链接。同一地址已经入库的不再下载。
"""

import time
from datetime import date, datetime, timedelta
from typing import Any, ClassVar, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import get_settings
from app.providers.errors import ProviderError
from app.providers.polite import PoliteError, contact_email, live_client
from app.repositories.corpus import CNINFO_SOURCE, SEC_SOURCE, CorpusRepository
from app.repositories.instruments import InstrumentRecord
from app.repositories.sync_jobs import SyncJobRepository
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.corpus import _passage
from app.services.assistant.tools.market import _resolve
from app.services.cninfo_filings import CNINFO_HOSTS, CninfoCatalog, store_filing
from app.services.filing_meta import FilingHit
from app.services.pdf_text import extract_pages
from app.services.sec_filings import SEC_HOSTS, SecCatalog, pages_from_body

BUDGET_SECONDS = 20.0
SOURCE = "巨潮资讯"
_SEC_SOURCE_LABEL = "美国证券交易委员会"
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_NO_CONTACT = "没有配置可用的联系邮箱（SEC_USER_AGENT），不能抓取公告"


class FilingFetchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(
        description="instrument_search 返回的证券 id", min_length=1, max_length=64
    )
    report_type: (
        Literal["annual", "semiannual", "q1", "q3", "forecast", "express", "inquiry"] | None
    ) = Field(
        default=None, description="不填则取这段时间里最新的一份定期报告、业绩预告、快报或问询函"
    )
    period: str | None = Field(
        default=None, max_length=16, description="期次，如 2026Q3、2026H1、2026"
    )
    since: date | None = Field(default=None, description="公告发布日的起点，默认按期次或近 120 天")
    until: date | None = Field(default=None, description="公告发布日的终点，默认今天")

    @model_validator(mode="after")
    def _window(self) -> "FilingFetchArgs":
        if self.since is not None and self.until is not None and self.since > self.until:
            raise ValueError("since 不能晚于 until")
        return self


class FilingFetchTool:
    name: ClassVar[str] = "filing_fetch"
    description: ClassVar[str] = (
        "抓取公告正文并放进语料库。A 股是巨潮上的业绩预告、快报、定期报告、问询函；"
        "美股是 8-K（优先新闻稿 Exhibit 99.1）。先用 instrument_search 拿到 instrument_id。"
        "返回的原文片段按 [n] 引用。抓过的不会重复下载；20 秒内读不完会转后台，这时只有标题和链接。"
    )
    args_model: ClassVar[type[BaseModel]] = FilingFetchArgs

    def __init__(
        self,
        catalog: CninfoCatalog | None = None,
        *,
        extract: Any = extract_pages,
        clock: Any = time.monotonic,
        budget: float = BUDGET_SECONDS,
        load: Any = None,
        store: Any = None,
        enqueue: Any = None,
        resolve: Any = None,
        sec: Any = None,
    ) -> None:
        self._catalog = catalog
        self._extract = extract
        self._clock = clock
        self._budget = budget
        self._load = load
        self._store = store
        self._enqueue = enqueue
        self._resolve = resolve
        self._sec = sec

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        started = self._clock()
        assert isinstance(args, FilingFetchArgs)
        found = (self._resolve or _resolve)(ctx, args.instrument_id)
        if isinstance(found, str):
            return unavailable(self.name, "读取公告", ctx.today_cn, found, SOURCE)
        if found.market == "US":
            return self._run_us(ctx, args, found, started)
        if found.market != "CN":
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, "目前只抓 A 股公告和美股 8-K", SOURCE
            )
        code = "".join(char for char in found.symbol if char.isdigit())
        if len(code) != 6:
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, "不是 6 位 A 股代码", SOURCE
            )
        catalog = self._live_catalog()
        if catalog is None:
            return unavailable(self.name, found.display_symbol, ctx.today_cn, _NO_CONTACT, SOURCE)
        start, end = _window(ctx.today_cn, args)
        if start > end:
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, "还没到这个期次", SOURCE
            )
        try:
            hits = catalog.list_stock(code=code, exchange=found.exchange, start=start, end=end)
        except PoliteError as exc:
            return unavailable(self.name, found.display_symbol, ctx.today_cn, str(exc), SOURCE)
        hit = _choose(hits, args.report_type, args.period)
        if hit is None:
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, "这段时间没有对应的公告", SOURCE
            )
        stored = self._stored(ctx, hit.url)
        if stored:
            return _ready(hit, stored)
        if self._clock() > started + self._budget:
            self._hand_off(ctx, found, hit, source="cninfo")
            return _deferred(hit, ctx.today_cn)
        try:
            pdf = catalog.download(hit.url)
            pages = self._extract(
                pdf,
                time_budget_seconds=max(1.0, started + self._budget - self._clock()),
                max_pages=get_settings().upload_max_pages,
            )
        except PoliteError as exc:
            if exc.code == "TOO_LARGE":
                return unavailable(
                    self.name,
                    hit.title,
                    _day(hit),
                    f"文件超过 5 MB，请直接看原文：{hit.url}",
                    SOURCE,
                )
            return unavailable(self.name, hit.title, _day(hit), str(exc), SOURCE)
        if not pages:
            return unavailable(self.name, hit.title, _day(hit), "这份公告没有抽出文字", SOURCE)
        self._save(ctx, hit, pages, found.id)
        stored = self._stored(ctx, hit.url)
        return (
            _ready(hit, stored)
            if stored
            else unavailable(self.name, hit.title, _day(hit), "公告没有写入语料库", SOURCE)
        )

    def _run_us(
        self, ctx: ToolContext, args: FilingFetchArgs, found: InstrumentRecord, started: float
    ) -> ToolResult:
        if args.report_type is not None:
            return unavailable(
                self.name,
                found.display_symbol,
                ctx.today_cn,
                "美股目前只抓 8-K，定期报告还不抓",
                _SEC_SOURCE_LABEL,
            )
        if not found.cik:
            return unavailable(
                self.name,
                found.display_symbol,
                ctx.today_cn,
                "这只美股没有 CIK，不能查 EDGAR",
                _SEC_SOURCE_LABEL,
            )
        catalog = self._sec if self._sec is not None else self._live_sec()
        if catalog is None:
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, _NO_CONTACT, _SEC_SOURCE_LABEL
            )
        start, end = _window(ctx.today_cn, args)
        try:
            listed = catalog.list_8k(
                cik=found.cik,
                name=found.name_en or found.display_symbol,
                symbol=found.symbol,
                start=start,
            )
        except PoliteError as exc:
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, str(exc), _SEC_SOURCE_LABEL
            )
        chosen = tuple(
            hit for hit in listed if start <= hit.published_at.astimezone(_SHANGHAI).date() <= end
        )
        hit = _choose(chosen, None, None)
        if hit is None:
            return unavailable(
                self.name, found.display_symbol, ctx.today_cn, "这段时间没有 8-K", _SEC_SOURCE_LABEL
            )
        stored = self._stored(ctx, hit.url, SEC_SOURCE)
        if stored:
            return _ready(hit, stored, _SEC_SOURCE_LABEL)
        if self._clock() > started + self._budget:
            self._hand_off(ctx, found, hit, source="sec")
            return _deferred(hit, ctx.today_cn, _SEC_SOURCE_LABEL)
        try:
            _body_url, data = catalog.download_body(hit.url)
            pages = pages_from_body(
                data,
                extract=self._extract,
                time_budget_seconds=max(1.0, started + self._budget - self._clock()),
                max_pages=get_settings().upload_max_pages,
            )
        except PoliteError as exc:
            return unavailable(self.name, hit.title, _day(hit), str(exc), _SEC_SOURCE_LABEL)
        if not pages:
            return unavailable(
                self.name, hit.title, _day(hit), "这份 8-K 没有抽出文字", _SEC_SOURCE_LABEL
            )
        self._save(ctx, hit, pages, found.id, source_id=SEC_SOURCE, publisher=_SEC_SOURCE_LABEL)
        stored = self._stored(ctx, hit.url, SEC_SOURCE)
        return (
            _ready(hit, stored, _SEC_SOURCE_LABEL)
            if stored
            else unavailable(
                self.name, hit.title, _day(hit), "公告没有写入语料库", _SEC_SOURCE_LABEL
            )
        )

    def _live_sec(self) -> SecCatalog | None:
        email = contact_email(get_settings().sec_user_agent)
        if email is None:
            return None
        return SecCatalog(live_client(email, SEC_HOSTS))

    def _stored(self, ctx: ToolContext, url: str, source_id: UUID = CNINFO_SOURCE) -> list[Any]:
        if self._load is not None:
            return list(self._load(url))
        return CorpusRepository(ctx.engine).latest_passages(source_id=source_id, url=url)

    def _save(
        self,
        ctx: ToolContext,
        hit: FilingHit,
        pages: list[Any],
        instrument_id: UUID,
        *,
        source_id: UUID = CNINFO_SOURCE,
        publisher: str = SOURCE,
    ) -> None:
        if self._store is not None:
            self._store(hit, pages, instrument_id)
            return
        note = (
            "SEC EDGAR 公开文件，仅作研究引用，保留原文链接"
            if source_id == SEC_SOURCE
            else "巨潮资讯公开公告，仅作研究引用，保留原文链接"
        )
        store_filing(
            CorpusRepository(ctx.engine),
            hit,
            pages,
            instrument_id=instrument_id,
            source_id=source_id,
            publisher=publisher,
            license_note=note,
        )

    def _hand_off(
        self,
        ctx: ToolContext,
        instrument: InstrumentRecord,
        hit: FilingHit,
        *,
        source: str = "cninfo",
    ) -> None:
        if self._enqueue is not None:
            self._enqueue(hit)
            return
        _enqueue(ctx, instrument, hit, source=source)

    def _live_catalog(self) -> CninfoCatalog | None:
        if self._catalog is not None:
            return self._catalog
        email = contact_email(get_settings().sec_user_agent)
        if email is None:
            return None
        return CninfoCatalog(live_client(email, CNINFO_HOSTS))


def _window(today: date, args: FilingFetchArgs) -> tuple[date, date]:
    if args.since is not None or args.until is not None:
        start = args.since or today - timedelta(days=120)
        end = args.until or today
    elif args.period and len(args.period) == 4 and args.period.isdigit():
        year = int(args.period)
        start, end = date(year, 1, 1), date(year, 12, 31)
    elif args.period and len(args.period) == 6 and args.period[4] in {"Q", "H"}:
        year = int(args.period[:4])
        part = int(args.period[5])
        month = (part - 1) * 3 + 1 if args.period[4] == "Q" else (1 if part == 1 else 7)
        start, end = date(year, month, 1), date(year, 12, 31)
    else:
        start, end = today - timedelta(days=120), today
    return start, min(end, today)


def _choose(
    hits: tuple[FilingHit, ...], report_type: str | None, period: str | None
) -> FilingHit | None:
    matched = [
        hit
        for hit in hits
        if (report_type is None or hit.report_type == report_type)
        and (period is None or hit.period == period)
        and (report_type is not None or hit.report_type != "other")
    ]
    return max(matched, key=lambda hit: hit.published_at) if matched else None


def _ready(hit: FilingHit, stored: list[Any], source: str = SOURCE) -> ToolResult:
    return ToolResult(
        tool="filing_fetch",
        title=hit.title,
        columns=(
            Column("published", "发布日", "", "date"),
            Column("report", "类型", "", "text"),
            Column("url", "原文", "", "text"),
        ),
        rows=(
            {
                "published": _day(hit).isoformat(),
                "report": hit.report_type or "",
                "url": hit.url,
            },
        ),
        as_of=_day(hit),
        source=source,
        passages=tuple(_passage(item) for item in stored),
    )


def _deferred(hit: FilingHit, today: date, source: str = SOURCE) -> ToolResult:
    return unavailable(
        "filing_fetch",
        hit.title,
        today,
        f"公告还在读取，稍后再问。{hit.title} {hit.url}",
        source,
    )


def _enqueue(
    ctx: ToolContext, instrument: InstrumentRecord, hit: FilingHit, *, source: str = "cninfo"
) -> None:
    jobs = SyncJobRepository(ctx.engine)
    upstream = "https://www.sec.gov" if source == "sec" else "https://www.cninfo.com.cn"
    provider_id = jobs.provider_id(
        code=source,
        name=_SEC_SOURCE_LABEL if source == "sec" else SOURCE,
        config_public={"upstream": upstream},
    )
    jobs.enqueue(
        job_type="corpus_filing",
        provider_id=provider_id,
        scope={
            "source": source,
            "url": hit.url,
            "title": hit.title,
            "symbol": hit.code,
            "published_at": hit.published_at.isoformat(),
            "report_type": hit.report_type,
            "period": hit.period or "",
            "instrument_id": str(instrument.id),
        },
        dedupe_key=f"corpus-filing:{hit.url}",
        requested_by=ctx.user_id,
        now=ctx.now,
        max_attempts=2,
    )


def _day(hit: FilingHit) -> date:
    published = hit.published_at
    if published.tzinfo is not None:
        published = published.astimezone(_SHANGHAI)
    return published.date()


def resume_filing(engine: Any, scope: dict[str, object]) -> int:
    """worker 接着读一份超时的公告。返回写入的块数；已经入库则返回 0。"""

    from sqlalchemy import Engine

    from app.services.pdf_text import extract_pages as extract

    assert isinstance(engine, Engine)
    url = _scope_text(scope, "url")
    sec = scope.get("source") == "sec"
    source_id = SEC_SOURCE if sec else CNINFO_SOURCE
    repo = CorpusRepository(engine)
    if repo.latest_passages(source_id=source_id, url=url, limit=1):
        return 0
    email = contact_email(get_settings().sec_user_agent)
    if email is None:
        raise ProviderError(code="DATA_UNAVAILABLE", message=_NO_CONTACT, status_code=503)
    try:
        if sec:
            _body_url, payload = SecCatalog(live_client(email, SEC_HOSTS)).download_body(url)
            pages = pages_from_body(
                payload,
                extract=extract,
                time_budget_seconds=get_settings().upload_parse_time_budget_seconds,
                max_pages=get_settings().upload_max_pages,
            )
        else:
            payload = CninfoCatalog(live_client(email, CNINFO_HOSTS)).download(url)
            pages = extract(
                payload,
                time_budget_seconds=get_settings().upload_parse_time_budget_seconds,
                max_pages=get_settings().upload_max_pages,
            )
    except PoliteError as exc:
        raise ProviderError(code=exc.code, message=str(exc), status_code=503) from exc
    published = datetime.fromisoformat(_scope_text(scope, "published_at"))
    raw_id = scope.get("instrument_id")
    instrument_id = UUID(raw_id) if isinstance(raw_id, str) and raw_id else None
    hit = FilingHit(
        code=_scope_text(scope, "symbol"),
        title=_scope_text(scope, "title"),
        url=url,
        published_at=published,
        report_type=_scope_text(scope, "report_type"),
        period=_scope_text(scope, "period") or None,
    )
    saved = store_filing(
        repo,
        hit,
        pages,
        instrument_id=instrument_id,
        source_id=source_id,
        publisher=_SEC_SOURCE_LABEL if sec else SOURCE,
        license_note=(
            "SEC EDGAR 公开文件，仅作研究引用，保留原文链接"
            if sec
            else "巨潮资讯公开公告，仅作研究引用，保留原文链接"
        ),
    )
    return 0 if saved is None else len(pages)


def _scope_text(scope: dict[str, object], key: str) -> str:
    value = scope.get(key)
    return value.strip() if isinstance(value, str) else ""
