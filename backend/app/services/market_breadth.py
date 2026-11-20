"""全 A（沪深京）涨跌家数：逐只报价统计。

东财 `getTopicZDFenBu` 只给 -11 ~ +11 的涨跌幅直方图，创业板/科创板 ±20%、北证 ±30%
的个股一律落不进档位；而且它的快照本身也不是全市场。这里改用腾讯报价接口逐只取价，
按「现价 vs 昨收」自己数，成交额为 0（停牌/无成交）的不计入。

北交所个股不在 `instruments` 表里（universe 同步只覆盖沪深），代码表从巨潮
`szse_stock.json` 取（4/8/9 开头、排除退市）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import Engine, select

from app.db.models.market import Instrument
from app.providers import tencent_quote as tq

BSE_LIST_URL = "http://www.cninfo.com.cn/new/data/szse_stock.json"
BSE_PREFIXES = ("43", "83", "87", "88", "920")
_SIX_DIGITS = re.compile(r"\d{6}")
_LOCAL_CODE = re.compile(r"(?:sh|sz|bj)\d{6}")
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinScope research"
_QUOTE_HEADERS = {"User-Agent": _UA, "Referer": "https://gu.qq.com/"}
_QUOTE_BATCH = 60
_CONCURRENCY = 8
_TTL_LIVE = 30
_TTL_IDLE = 600


@dataclass(frozen=True, slots=True)
class BreadthCounts:
    """全市场涨跌家数（已剔除停牌/无成交）。"""

    up: int
    down: int
    flat: int
    total: int
    source: str = "腾讯财经 逐只报价（沪深京）"

    def note(self) -> str:
        return (
            f"涨跌家数按沪深京全市场逐只统计（{self.total} 只，成交额为 0 的停牌股不计入），"
            "与东财涨跌分布直方图口径不同。"
        )


def bse_codes(payload: bytes) -> list[str]:
    """从巨潮股票列表里筛出北交所 A 股代码（6 位）。

    退市整理期的个股（如「云创退」）仍在交易，行情软件的家数也把它们算在内，
    因此这里不按名称过滤，只按代码段与类别过滤。
    """

    try:
        doc = json.loads(payload.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    rows = doc.get("stockList") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return []
    out: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        category = row.get("category")
        if len(code) != 6 or not code.isdigit():
            continue
        if not code.startswith(BSE_PREFIXES):
            continue
        if category not in (None, "A股"):
            continue
        out.add(code)
    return sorted(out)


def count_breadth(quotes: Iterable[tq.Quote]) -> BreadthCounts:
    """按现价与昨收统计涨跌家数；停牌（成交额为 0）与无效报价不计入。"""

    up = down = flat = 0
    for quote in quotes:
        price = quote.price
        prev = quote.prev_close
        amount = quote.amount_yuan
        if price is None or prev is None or price <= 0 or prev <= 0:
            continue
        if amount is None or amount <= 0:
            continue
        if price > prev:
            up += 1
        elif price < prev:
            down += 1
        else:
            flat += 1
    return BreadthCounts(up=up, down=down, flat=flat, total=up + down + flat)


def universe_codes(engine: Engine) -> list[str]:
    """股票池：`instruments` 里的沪深个股 + 巨潮给的北交所个股（本地代码，如 sh600000）。"""

    with engine.connect() as conn:
        rows = conn.execute(
            select(Instrument.display_symbol).where(
                Instrument.is_active.is_(True),
                Instrument.instrument_type == "STOCK",
                Instrument.exchange.in_(("SSE", "SZSE")),
            )
        ).scalars()
        codes = {code.split(".")[0].strip() for code in rows}
    mapped = [tq.a_share_code(code) for code in codes if _SIX_DIGITS.fullmatch(code)]
    mapped = [code for code in mapped if _LOCAL_CODE.fullmatch(code)]
    try:
        payload = httpx.get(
            BSE_LIST_URL,
            headers={"User-Agent": _UA},
            timeout=20.0,
            follow_redirects=True,
        ).content
        mapped.extend(f"bj{code}" for code in bse_codes(payload))
    except httpx.HTTPError:
        pass
    return sorted(set(mapped))


async def _fetch_quotes(codes: list[str]) -> dict[str, tq.Quote]:
    semaphore = asyncio.Semaphore(_CONCURRENCY)

    async with httpx.AsyncClient(
        headers=_QUOTE_HEADERS, timeout=20.0, follow_redirects=True
    ) as client:

        async def batch(chunk: list[str]) -> dict[str, tq.Quote]:
            async with semaphore:
                try:
                    payload = (await client.get(tq.quote_url(chunk))).content
                except httpx.HTTPError:
                    return {}
            return tq.parse_quotes(payload)

        chunks = [codes[i : i + _QUOTE_BATCH] for i in range(0, len(codes), _QUOTE_BATCH)]
        results = await asyncio.gather(*(batch(chunk) for chunk in chunks))

    merged: dict[str, tq.Quote] = {}
    for part in results:
        merged.update(part)
    return merged


def _cache_ttl() -> int:
    local = time.localtime()
    in_session = local.tm_wday < 5 and (9 * 60 + 15) <= (local.tm_hour * 60 + local.tm_min) <= (
        15 * 60 + 5
    )
    return _TTL_LIVE if in_session else _TTL_IDLE


_BREADTH_CACHE: dict[str, tuple[float, BreadthCounts]] = {}
_UNIVERSE_CACHE: dict[str, tuple[float, list[str]]] = {}


def invalidate() -> None:
    """测试与手动刷新用：清空进程内缓存。"""

    _BREADTH_CACHE.clear()
    _UNIVERSE_CACHE.clear()


async def breadth_counts(engine: Engine) -> BreadthCounts | None:
    """全市场涨跌家数；拿不到任何报价时返回 None（调用方回落到东财直方图）。"""

    stamp = time.monotonic()
    cached = _BREADTH_CACHE.get("all")
    if cached is not None and stamp - cached[0] < _cache_ttl():
        return cached[1]

    universe = _UNIVERSE_CACHE.get("all")
    if universe is None or stamp - universe[0] > 6 * 3600:
        codes = await asyncio.to_thread(universe_codes, engine)
        _UNIVERSE_CACHE["all"] = (stamp, codes)
    else:
        codes = universe[1]

    if not codes:
        return None
    counts = count_breadth((await _fetch_quotes(codes)).values())
    if counts.total == 0:
        return None
    _BREADTH_CACHE["all"] = (stamp, counts)
    return counts


def __getattr__(name: str) -> Any:  # pragma: no cover - 便于调试时按需取缓存
    if name == "cache":
        return _BREADTH_CACHE
    raise AttributeError(name)
