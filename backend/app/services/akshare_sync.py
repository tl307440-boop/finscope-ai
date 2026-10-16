import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import httpx
from sqlalchemy import Engine

from app.providers import tencent_bars
from app.providers.akshare_bars import (
    UNSUPPORTED_SYMBOL_MESSAGE,
    AdjustmentCode,
    DailyTarget,
    dedupe_bars,
    kline_url,
    parse_klines,
    target_for,
)
from app.providers.errors import ProviderDisconnected, ProviderError
from app.providers.http_client import ResilientHttpClient
from app.providers.httpx_transport import HttpxTransport
from app.providers.quality import RawBar
from app.repositories.instruments import InstrumentRepository
from app.repositories.market_bars import MarketBarRepository
from app.services.ingest import ingest_bars
from app.utils.identifiers import parse_search_key

BarFetcher = Callable[[str, date, date, AdjustmentCode], Awaitable[tuple[RawBar, ...]]]
# 单个行情源的抓取函数：拿的是已解析好的抓取目标，返回未经质检的原始日线。
SourceFetcher = Callable[[DailyTarget, date, date, AdjustmentCode], Awaitable[tuple[RawBar, ...]]]
_MAX_WINDOW_DAYS = 3660
# 只有股票和指数有日线；可转债、基金之类暂不抓。
_BAR_INSTRUMENT_TYPES = ("STOCK", "INDEX")


def _client() -> httpx.AsyncClient:
    # httpx 默认 trust_env=True，会读 Windows 注册表里的系统代理。
    # 境内行情源一律直连：代理只是翻墙通道，不该成为境内 CDN 的必经之路。
    # 这条链路的 RemoteProtocolError 属于可重试失败，所以把次数放宽。
    return httpx.AsyncClient(timeout=httpx.Timeout(10.0), trust_env=False)


async def _get(url: str) -> bytes:
    async with _client() as client:
        http = ResilientHttpClient(
            HttpxTransport(client),
            sleeper=asyncio.sleep,
            min_interval_seconds=0.2,
            max_attempts=4,
        )
        return await http.get(url)


async def _fetch_eastmoney(
    target: DailyTarget,
    start: date,
    end: date,
    adjustment: AdjustmentCode,
) -> tuple[RawBar, ...]:
    payload = await _get(
        kline_url(
            exchange=target.exchange,
            symbol=target.symbol,
            start=start,
            end=end,
            adjustment=adjustment,
        )
    )
    return parse_klines(payload, display_symbol=target.display_symbol, adjustment=adjustment)


async def _fetch_tencent(
    target: DailyTarget,
    start: date,
    end: date,
    adjustment: AdjustmentCode,
) -> tuple[RawBar, ...]:
    payload = await _get(
        tencent_bars.kline_url(
            exchange=target.exchange,
            symbol=target.symbol,
            start=start,
            end=end,
            adjustment=adjustment,
        )
    )
    return tencent_bars.parse_klines(payload, target=target, adjustment=adjustment)


# 顺序即优先级：东方财富字段最全（含成交额），取不到再退到腾讯。
_SOURCES: tuple[tuple[str, SourceFetcher], ...] = (
    ("东方财富", _fetch_eastmoney),
    ("腾讯行情", _fetch_tencent),
)


async def fetch_bars(
    display_symbol: str,
    start: date,
    end: date,
    adjustment: AdjustmentCode,
) -> tuple[RawBar, ...]:
    """依次尝试各个行情源，全部失败才报错。

    单个源失败不是致命错误——这是两个独立第三方站点的日常波动，
    所以把每个源的失败原因收集起来一起报，而不是让第一个 500 直接冒出去。
    """
    target = target_for(display_symbol)
    if target is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message=UNSUPPORTED_SYMBOL_MESSAGE,
            status_code=422,
        )
    problems: list[str] = []
    failed = 0
    for name, fetcher in _SOURCES:
        try:
            bars = await fetcher(target, start, end, adjustment)
        except (ProviderError, ProviderDisconnected) as exc:
            problems.append(f"{name}：{_problem_text(exc)}")
            failed += 1
            continue
        if bars:
            return bars
        problems.append(f"{name}：区间内没有返回任何交易日")
    if failed == 0:
        # 每个源都正常应答、只是区间里没有交易日（周末、休市，或还没收盘）。
        # 这不是故障；若是源站长期不更新，新鲜度监控会按「最后一根日线」报过期。
        raise ProviderError(
            code="NO_ROWS",
            message="区间内没有新的交易日",
            status_code=200,
        )
    raise ProviderError(
        code="UPSTREAM_UNAVAILABLE",
        message="所有行情源都没取到日线（" + "；".join(problems) + "）",
        status_code=502,
    )


def _problem_text(exc: ProviderError | ProviderDisconnected) -> str:
    if isinstance(exc, ProviderError):
        return exc.message
    return "连接中断"


def valid_window(start: date, end: date) -> bool:
    if end < start:
        return False
    return (end - start).days <= _MAX_WINDOW_DAYS


@dataclass(frozen=True)
class AkshareSyncOutcome:
    rows_written: int
    rows_rejected: int
    rows_for_review: int
    duplicates_skipped: int


def apply_bars(
    engine: Engine,
    bars: tuple[RawBar, ...],
    *,
    display_symbol: str,
    adjustment: AdjustmentCode,
) -> AkshareSyncOutcome:
    target = target_for(display_symbol)
    if target is None:
        raise ProviderError(
            code="VALIDATION_ERROR",
            message=UNSUPPORTED_SYMBOL_MESSAGE,
            status_code=422,
        )
    kept, skipped = dedupe_bars(bars)
    instrument = _instrument(engine, target.display_symbol)
    repository = MarketBarRepository(engine)
    provider_id = repository.ensure_akshare_provider()
    outcome = ingest_bars(
        kept,
        repository,
        instrument_id=instrument,
        provider_id=provider_id,
    )
    if outcome.error is not None:
        raise outcome.error
    return AkshareSyncOutcome(
        rows_written=outcome.rows_written,
        rows_rejected=outcome.rows_rejected,
        rows_for_review=outcome.rows_for_review,
        duplicates_skipped=skipped,
    )


def _instrument(engine: Engine, display_symbol: str) -> UUID:
    records = InstrumentRepository(engine).search(
        parse_search_key(display_symbol),
        market="CN",
        limit=5,
    )
    matches = [
        record
        for record in records
        if record.display_symbol == display_symbol
        and record.instrument_type in _BAR_INSTRUMENT_TYPES
    ]
    if len(matches) != 1:
        raise ProviderError(code="NOT_FOUND", message="暂无该证券", status_code=404)
    return matches[0].id
