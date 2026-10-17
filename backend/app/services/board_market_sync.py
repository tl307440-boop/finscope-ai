"""收盘后把东财板块行情快照与板块日线写入数据库。

- 交易日以沪深300 日线的最后一根为准：今天没有新 K 线（周末、休市）就不写快照，
  避免把上一交易日的数据记到今天名下。
- 快照只在收盘后（或下一交易日开盘前）抓取，交易时段内的数字不是收盘值，不入库。
- 日线按每个板块已入库的最后一天增量补，最后一天重抓一次覆盖（收盘后东财可能修正）。
- 连续 3 次上游失败就停，不在频控期间继续打上游；已写入的保留，整体按任务重试策略重来。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from typing import Protocol

from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError
from app.services.industry_market import BENCHMARK_SECID, Gateway
from app.utils.market_time import SHANGHAI

logger = logging.getLogger(__name__)

HISTORY_DAYS = 420
MAX_CONSECUTIVE_FAILURES = 3
BAR_LEVELS = (1, 2)
_CLOSE_SETTLED = time(15, 5)
_PRE_OPEN = time(9, 15)


class BoardStore(Protocol):
    def upsert_snapshots(
        self,
        trade_date: date,
        rows: Sequence[tuple[em.BoardNode, em.BoardQuote]],
        fetched_at: datetime,
    ) -> int: ...

    def upsert_bars(self, secid: str, bars: Sequence[em.BoardBar], fetched_at: datetime) -> int: ...

    def last_bar_dates(self, secids: Sequence[str]) -> dict[str, date]: ...


def snapshot_allowed(now_local: datetime, session: date) -> bool:
    """当前时刻看到的板块行情是否就是 session 那天的收盘值。"""

    today = now_local.date()
    if today == session:
        return now_local.time() >= _CLOSE_SETTLED
    if today > session:
        return now_local.weekday() >= 5 or now_local.time() < _PRE_OPEN
    return False


def bar_window(last: date | None, today: date) -> date:
    return last if last is not None else today - timedelta(days=HISTORY_DAYS)


async def sync_board_market(store: BoardStore, gateway: Gateway, now: datetime) -> int:
    local = now.astimezone(SHANGHAI)
    today = local.date()
    written = 0

    known = store.last_bar_dates([BENCHMARK_SECID])
    bench = await _klines(
        gateway, BENCHMARK_SECID, bar_window(known.get(BENCHMARK_SECID), today), today
    )
    written += store.upsert_bars(BENCHMARK_SECID, bench, now)
    session = bench[-1].trade_date if bench else known.get(BENCHMARK_SECID)
    if session is None:
        raise ProviderError(
            code="NO_ROWS", message="拿不到沪深300 日线，无法确定交易日", status_code=503
        )

    tree = em.parse_board_tree(await gateway.get(em.BOARD_TREE_URL, em.EASTMONEY_HEADERS))
    if snapshot_allowed(local, session):
        boards = [node for node in tree if node.kind in ("industry", "concept")]
        by_code = {node.code: node for node in boards}
        quotes: list[em.BoardQuote] = []
        for start in range(0, len(boards), em.PAGE_SIZE):
            chunk = [node.code for node in boards[start : start + em.PAGE_SIZE]]
            payload = await gateway.get(em.quote_url(chunk), em.EASTMONEY_HEADERS)
            quotes.extend(em.parse_board_quotes(payload))
        rows = [(by_code[quote.code], quote) for quote in quotes if quote.code in by_code]
        written += store.upsert_snapshots(session, rows, now)
        logger.info("board snapshot session=%s boards=%s", session, len(rows))
    else:
        logger.info("board snapshot skipped session=%s local=%s", session, local.isoformat())

    targets = [
        em.board_secid(node.code)
        for node in tree
        if node.kind == "industry" and node.level in BAR_LEVELS
    ]
    last = store.last_bar_dates(targets)
    failures = 0
    consecutive = 0
    for secid in targets:
        if last.get(secid) is not None and last[secid] >= session:
            continue
        try:
            bars = await _klines(gateway, secid, bar_window(last.get(secid), today), today)
        except ProviderError as error:
            failures += 1
            consecutive += 1
            logger.warning("board bars failed secid=%s reason=%s", secid, error.message)
            if consecutive >= MAX_CONSECUTIVE_FAILURES:
                break
            continue
        consecutive = 0
        written += store.upsert_bars(secid, bars, now)
    if failures:
        raise ProviderError(
            code="PARTIAL_SYNC",
            message=f"{failures} 个板块日线抓取失败（已写入 {written} 行），稍后重试",
            status_code=503,
        )
    return written


async def _klines(gateway: Gateway, secid: str, start: date, end: date) -> tuple[em.BoardBar, ...]:
    payload = await gateway.get(em.kline_url(secid, start, end), em.EASTMONEY_HEADERS)
    return em.parse_klines(payload)
