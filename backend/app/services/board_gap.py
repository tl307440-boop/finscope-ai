"""板块快照落后于最近一个收盘日时，用新浪板块榜补当日涨跌。

新浪只有等权涨跌幅、领涨股，没有 5 日 / 60 日、市盈率、主力净流入、涨跌家数。
名称对得上、并且新浪给了涨跌幅，才补当日涨跌；领涨股还要新浪给出名称。
其余列留着快照里的原值。行业榜和概念榜一边失败时，另一边仍然可用。
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Protocol

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


def index_sina(boards: tuple[sina.SinaBoard, ...]) -> dict[str, sina.SinaBoard]:
    found: dict[str, sina.SinaBoard] = {}
    for board in boards:
        name = board.name.strip()
        if name:
            found.setdefault(name, board)
    return found


async def _load_sina(gateway: QuoteGateway, url: str) -> tuple[sina.SinaBoard, ...]:
    return sina.parse_boards(await gateway.get(url, sina.SINA_HEADERS))


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
