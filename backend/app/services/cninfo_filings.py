"""巨潮公告目录与正文入库（知识库设计 §5.1）。

目录沿用已有的查询参数。正文只下载 `static.cninfo.com.cn` 上的 PDF，
解析和切块走语料库自己的参数。同一地址已经入库就不再下载。
"""

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, text

from app.core.config import get_settings
from app.providers.disclosures import (
    CNINFO_QUERY,
    CNINFO_STATIC,
    CNINFO_STOCK_LIST,
    CninfoStock,
    cninfo_market_form,
    cninfo_query_form,
    parse_cninfo_market,
    parse_cninfo_stock_list,
)
from app.providers.errors import ProviderError
from app.providers.polite import PoliteClient, PoliteError, contact_email, live_client
from app.repositories.corpus import CNINFO_SOURCE, CorpusRepository, NewDocument
from app.services.corpus_ingest import content_hash, corpus_chunks
from app.services.corpus_text import detect_language
from app.services.filing_meta import FilingHit, period_of, report_type_of
from app.services.pdf_text import PageText, PdfTextError, extract_pages

CNINFO_HOSTS = frozenset({"www.cninfo.com.cn", "static.cninfo.com.cn"})
FORECAST_CATEGORY = "category_yjygjxz_szsh"
EXPRESS_KEYWORD = "业绩快报"
EARNINGS_MONTHS = frozenset({1, 4, 7, 8, 10})
MAX_DOWNLOADS = 15
_BODY_TYPES = frozenset({"forecast", "express", "inquiry", "annual", "semiannual", "q1", "q3"})
_FORM = "application/x-www-form-urlencoded; charset=UTF-8"
_SHANGHAI = ZoneInfo("Asia/Shanghai")


class CninfoCatalog:
    def __init__(self, client: PoliteClient, stocks: dict[str, CninfoStock] | None = None) -> None:
        self._client = client
        self._stocks = stocks

    def list_stock(
        self, *, code: str, exchange: str, start: date, end: date
    ) -> tuple[FilingHit, ...]:
        stock = self._org(code)
        if stock is None:
            return ()
        column = "sse" if exchange == "SH" else "szse"
        body = urlencode(
            cninfo_query_form(stock=stock, column=column, start=start, end=end, page_size=30)
        ).encode()
        payload = self._client.post(CNINFO_QUERY, body, _FORM)
        return _hits(parse_cninfo_market(payload), code)

    def list_market(
        self, *, start: date, end: date, category: str = "", searchkey: str = "", pages: int = 1
    ) -> tuple[FilingHit, ...]:
        found: list[FilingHit] = []
        for page in range(1, pages + 1):
            body = urlencode(
                cninfo_market_form(
                    start=start, end=end, page=page, category=category, searchkey=searchkey
                )
            ).encode()
            found.extend(_hits(parse_cninfo_market(self._client.post(CNINFO_QUERY, body, _FORM))))
        return tuple(found)

    def download(self, url: str) -> bytes:
        if not url.startswith(CNINFO_STATIC):
            from app.providers.polite import PoliteError

            raise PoliteError("HOST", "公告正文只从 static.cninfo.com.cn 下载")
        return self._client.get(url)

    def _org(self, code: str) -> CninfoStock | None:
        if self._stocks is None:
            self._stocks = parse_cninfo_stock_list(self._client.get(CNINFO_STOCK_LIST))
        return self._stocks.get(code)


def store_filing(
    repo: CorpusRepository,
    hit: FilingHit,
    pages: list[PageText],
    *,
    instrument_id: UUID | None,
    source_id: UUID = CNINFO_SOURCE,
    publisher: str = "巨潮资讯",
    license_note: str = "巨潮资讯公开公告，仅作研究引用，保留原文链接",
    kind: str = "filing",
    doc_type: str = "announcement",
) -> UUID | None:
    """把已经解析的公告页写入语料库。内容与最新版本相同则返回 None。"""

    chunks = corpus_chunks(
        pages,
        title=hit.title,
        publisher=publisher,
        symbol=hit.code,
        period=hit.period,
        report_type=hit.report_type,
    )
    if not chunks:
        return None
    body = "\n".join(chunk.content for chunk in chunks)
    return repo.save(
        NewDocument(
            source_id=source_id,
            canonical_url=hit.url,
            kind=kind,
            title=hit.title,
            doc_type=doc_type,
            language=detect_language(body),
            published_at=hit.published_at,
            content_hash=content_hash(chunks),
            authority="official",
            instrument_id=instrument_id,
            symbol=hit.code,
            period=hit.period,
            report_type=hit.report_type,
            license_note=license_note,
        ),
        chunks,
    )


def sync_recent(engine: Engine, mode: str) -> int:
    """定时任务：补最近两天还没入库的公告正文。`mode` 为 market（预告/快报）或 watchlist。"""

    email = contact_email(get_settings().sec_user_agent)
    if email is None:
        raise ProviderError(
            code="DATA_UNAVAILABLE",
            message="没有配置可用的联系邮箱（SEC_USER_AGENT），不能抓取公告",
            status_code=503,
        )
    catalog = CninfoCatalog(live_client(email, CNINFO_HOSTS))
    today = datetime.now(_SHANGHAI).date()
    start = today - timedelta(days=1)
    if mode == "market":
        hits = [
            *catalog.list_market(start=start, end=today, category=FORECAST_CATEGORY, pages=2),
            *catalog.list_market(start=start, end=today, searchkey=EXPRESS_KEYWORD),
        ]
        hits = [hit for hit in hits if hit.report_type in {"forecast", "express"}]
    elif mode == "watchlist":
        hits = []
        for code, exchange in _watch_codes(engine):
            hits.extend(
                hit
                for hit in catalog.list_stock(code=code, exchange=exchange, start=start, end=today)
                if hit.report_type in _BODY_TYPES
            )
    else:
        raise ProviderError(
            code="VALIDATION_ERROR", message="不认识的公告扫描范围", status_code=422
        )
    repo = CorpusRepository(engine)
    settings = get_settings()
    saved = 0
    seen: set[str] = set()
    for hit in hits:
        if hit.url in seen:
            continue
        seen.add(hit.url)
        if repo.latest_passages(source_id=CNINFO_SOURCE, url=hit.url, limit=1):
            continue
        if saved >= MAX_DOWNLOADS:
            break
        try:
            pdf = catalog.download(hit.url)
            pages = extract_pages(
                pdf,
                time_budget_seconds=settings.upload_parse_time_budget_seconds,
                max_pages=settings.upload_max_pages,
            )
        except (PoliteError, PdfTextError) as exc:
            if isinstance(exc, PoliteError) and exc.code == "BLOCKED":
                raise ProviderError(
                    code="UPSTREAM_BLOCKED", message=str(exc), status_code=403
                ) from exc
            continue
        if store_filing(repo, hit, pages, instrument_id=None) is not None:
            saved += 1
    return saved


def _watch_codes(engine: Engine) -> list[tuple[str, str]]:
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT DISTINCT i.symbol, i.exchange
                FROM watchlist_items w
                JOIN instruments i ON i.id = w.instrument_id
                WHERE i.market = 'CN'
                """
            )
        )
    return [
        (str(row[0]), str(row[1]))
        for row in rows
        if str(row[0]).isdigit() and len(str(row[0])) == 6
    ]


def _hits(rows: tuple[Any, ...], code: str = "") -> tuple[FilingHit, ...]:
    found: list[FilingHit] = []
    for row in rows:
        symbol = row.code or code
        found.append(
            FilingHit(
                code=symbol,
                title=row.title,
                url=row.url,
                published_at=row.published_at,
                report_type=report_type_of(row.title),
                period=period_of(row.title),
            )
        )
    return tuple(found)
