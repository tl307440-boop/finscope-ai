"""板块快照落后于最近一个收盘日时，用已经接上的公开行情补当日涨跌。

顺序：新浪行业榜和概念榜（有领涨股）优先，对不上的再看同花顺热门行业和热门概念。
两家都只有当日涨跌，没有 5 日 / 60 日、市盈率、主力净流入、涨跌家数。
名称相同，或只差「概念 / 板块 / 行业」这几个后缀时才补；不拿「机器人」去套「人形机器人」。
一边失败时另一边仍然可用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Protocol

from app.providers import eastmoney_boards as em
from app.providers import sina_market as sina
from app.providers.errors import ProviderError
from app.utils.market_time import SHANGHAI

# 与收盘入库相同：15:05 之后才把当天当成已经收盘。
SESSION_READY = time(15, 5)


class QuoteGateway(Protocol):
    async def get(self, url: str, headers: Mapping[str, str]) -> bytes: ...


def session_date(now: datetime) -> date:
    """最近一个已经收盘的 A 股交易日。周末和 15:05 前都退回上一交易日。"""

    local = now.astimezone(SHANGHAI)
    day = local.date()
    if day.weekday() >= 5 or local.time() < SESSION_READY:
        day -= timedelta(days=1)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
    return day


def needs_live_change(snapshot_day: date, now: datetime, *, historical: bool) -> bool:
    """回看某一天时不拿今天的新浪去填。只在问「现在」且快照还停在更早的收盘日时补。"""

    if historical:
        return False
    return snapshot_day < session_date(now)


SINA_SOURCE = "新浪实时"
THS_SOURCE = "同花顺热榜"
_SUFFIXES = ("概念", "板块", "行业")
_SESSION_OPEN = time(9, 30)


@dataclass(frozen=True)
class LiveChange:
    name: str
    change_pct: Decimal
    leader_name: str | None
    leader_change_pct: Decimal | None
    source: str


def live_change_date(now: datetime) -> date:
    """交易时段内用今天；开盘前和周末用最近一个已收盘日。"""

    local = now.astimezone(SHANGHAI)
    if local.weekday() < 5 and local.time() >= _SESSION_OPEN:
        return local.date()
    return session_date(now)


def _keys(name: str) -> tuple[str, ...]:
    text = name.strip()
    keys = [text]
    for suffix in _SUFFIXES:
        if text.endswith(suffix) and len(text) > len(suffix):
            keys.append(text[: -len(suffix)])
    return tuple(dict.fromkeys(key for key in keys if key))


def match_live(board_name: str, quotes: tuple[LiveChange, ...]) -> LiveChange | None:
    """先放入的来源优先。quotes 里新浪应排在同花顺前面。"""

    found: dict[str, LiveChange] = {}
    for quote in quotes:
        for key in _keys(quote.name):
            found.setdefault(key, quote)
    for key in _keys(board_name):
        hit = found.get(key)
        if hit is not None:
            return hit
    return None


def sina_changes(boards: tuple[sina.SinaBoard, ...]) -> tuple[LiveChange, ...]:
    changes: list[LiveChange] = []
    for board in boards:
        if board.change_pct is None or not board.name.strip():
            continue
        changes.append(
            LiveChange(
                board.name.strip(),
                board.change_pct,
                board.leader_name,
                board.leader_change_pct,
                SINA_SOURCE,
            )
        )
    return tuple(changes)


def ths_changes(plates: tuple[em.ThsHotPlate, ...]) -> tuple[LiveChange, ...]:
    changes: list[LiveChange] = []
    for plate in plates:
        if plate.change_pct is None or not plate.name.strip():
            continue
        changes.append(LiveChange(plate.name.strip(), plate.change_pct, None, None, THS_SOURCE))
    return tuple(changes)


async def _load_sina(gateway: QuoteGateway, url: str) -> tuple[sina.SinaBoard, ...]:
    return sina.parse_boards(await gateway.get(url, sina.SINA_HEADERS))


async def _load_ths(gateway: QuoteGateway, kind: str) -> tuple[em.ThsHotPlate, ...]:
    url = em.THS_HOT_PLATE_URL.format(kind=kind)
    return em.parse_ths_hot_plates(await gateway.get(url, em.THS_HEADERS))


async def fetch_ths_plates(gateway: QuoteGateway) -> tuple[em.ThsHotPlate, ...]:
    """热门行业和热门概念各取各的。一边失败时另一边仍然可用；两边都失败才抛错。"""

    outcomes = await asyncio.gather(
        _load_ths(gateway, "industry"),
        _load_ths(gateway, "concept"),
        return_exceptions=True,
    )
    plates: list[em.ThsHotPlate] = []
    errors: list[ProviderError] = []
    for outcome in outcomes:
        if isinstance(outcome, ProviderError):
            errors.append(outcome)
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            plates.extend(outcome)
    if errors and not plates:
        raise errors[0]
    return tuple(plates)


async def fetch_board_changes(gateway: QuoteGateway) -> tuple[LiveChange, ...]:
    """新浪全量榜在前，同花顺热榜在后。一家整榜失败时用另一家。两家都失败才抛错。"""

    outcomes = await asyncio.gather(
        fetch_sina_boards(gateway),
        fetch_ths_plates(gateway),
        return_exceptions=True,
    )
    changes: list[LiveChange] = []
    errors: list[ProviderError] = []
    sina_result, ths_result = outcomes
    if isinstance(sina_result, ProviderError):
        errors.append(sina_result)
    elif isinstance(sina_result, BaseException):
        raise sina_result
    else:
        changes.extend(sina_changes(sina_result))
    if isinstance(ths_result, ProviderError):
        errors.append(ths_result)
    elif isinstance(ths_result, BaseException):
        raise ths_result
    else:
        changes.extend(ths_changes(ths_result))
    if errors and not changes:
        raise errors[0]
    return tuple(changes)


async def fetch_sina_boards(gateway: QuoteGateway) -> tuple[sina.SinaBoard, ...]:
    """行业榜和概念榜各取各的。一边失败时另一边仍然可用；两边都失败才抛错。"""

    outcomes = await asyncio.gather(
        _load_sina(gateway, sina.INDUSTRY_URL),
        _load_sina(gateway, sina.CONCEPT_URL),
        return_exceptions=True,
    )
    boards: list[sina.SinaBoard] = []
    errors: list[ProviderError] = []
    for outcome in outcomes:
        if isinstance(outcome, ProviderError):
            errors.append(outcome)
        elif isinstance(outcome, BaseException):
            raise outcome
        else:
            boards.extend(outcome)
    if errors and not boards:
        raise errors[0]
    return tuple(boards)
