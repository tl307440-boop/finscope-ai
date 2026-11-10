"""韭研公社异动解析取数：签名窗口缓存 + 带登录态的 `action/field` + 解析成纯数据。

不编造：没配登录态、登录失效、签名算法不支持，都以 `ProviderError` 抛出，
调用方把原因写进 `meta.unavailable`，界面显示「暂无」。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import Engine

from app.providers import jygs as src
from app.providers.errors import ProviderError
from app.services.ashare_calc import as_doc, as_list, load_json, num, text
from app.services.industry_market import Gateway
from app.services.market_cache import Cached, TtlCache
from app.services.runtime_settings import effective_settings

_WINDOWS_TTL = 6 * 3600
_TTL_LIVE = 120
_TTL_IDLE = 600
_TTL_PAST = 6 * 3600
SOURCE_NAME = "韭研公社 异动解析"


@dataclass(frozen=True)
class JygsStockRow:
    code: str  # 6 位数字
    name: str
    sector: str
    sector_reason: str
    board_label: str
    price: float | None
    change_pct: float | None
    first_seal: str
    explain: str


@dataclass(frozen=True)
class JygsSectorRow:
    name: str
    reason: str
    stocks: list[JygsStockRow] = field(default_factory=list)


@dataclass(frozen=True)
class JygsActions:
    sectors: list[JygsSectorRow]

    @property
    def total(self) -> int:
        return sum(len(s.stocks) for s in self.sectors)

    def by_code(self) -> dict[str, JygsStockRow]:
        """同一只股票可能出现在多个板块：保留第一次出现（韭研按重要程度排序）。"""

        out: dict[str, JygsStockRow] = {}
        for sector in self.sectors:
            for row in sector.stocks:
                out.setdefault(row.code, row)
        return out


def _six(code: str) -> str:
    digits = "".join(ch for ch in code if ch.isdigit())
    return digits[-6:] if len(digits) >= 6 else ""


def parse_actions(data: Any) -> JygsActions:
    """解析 `action/field` 的 data。价格为分、涨幅为「百分比 × 100」，在这里换算。"""

    sectors: list[JygsSectorRow] = []
    for raw in as_list(data):
        doc = as_doc(raw)
        name = text(doc.get("name"))
        if not name or not text(doc.get("action_field_id")):
            continue  # 「简图」等占位项没有 action_field_id
        reason = text(doc.get("reason"))
        rows: list[JygsStockRow] = []
        for item in as_list(doc.get("list")):
            stock = as_doc(item)
            code = _six(text(stock.get("code")))
            if not code:
                continue
            info = as_doc(as_doc(stock.get("article")).get("action_info"))
            price, spread = num(info.get("price")), num(info.get("shares_range"))
            first_seal = text(info.get("time"))
            rows.append(
                JygsStockRow(
                    code=code,
                    name=text(stock.get("name")),
                    sector=name,
                    sector_reason=reason,
                    board_label=text(info.get("num")),
                    price=None if price is None or price <= 0 else round(price / 100, 2),
                    # 没有首封时间且涨幅为 0：多半是未采集到，而不是真的平盘。
                    change_pct=None
                    if spread is None or (spread == 0 and not first_seal)
                    else round(spread / 100, 2),
                    first_seal=first_seal,
                    explain=text(info.get("expound")),
                )
            )
        sectors.append(JygsSectorRow(name=name, reason=reason, stocks=rows))
    return JygsActions(sectors=sectors)


@dataclass
class JygsClient:
    gateway: Gateway
    cache: TtlCache
    session: str
    now: Callable[[], datetime]

    async def _windows(self) -> list[src.TokenWindow]:
        async def load() -> list[src.TokenWindow]:
            return src.parse_windows(await self.gateway.get(src.PAGE_URL, src.PAGE_HEADERS))

        return (await self.cache.get("jygs:windows", _WINDOWS_TTL, load)).value

    async def actions(self, day: date, ttl: int) -> Cached[JygsActions]:
        if not self.session.strip():
            raise ProviderError(code="NOT_CONFIGURED", message=src.NOT_CONFIGURED, status_code=503)

        async def load() -> JygsActions:
            stamp, token = src.sign(await self._windows(), self.now())
            headers: Mapping[str, str] = src.api_headers(stamp, token, self.session.strip())
            payload = await self.gateway.post(
                src.api_url("action/field"), src.field_body(day), headers
            )
            return parse_actions(src.check_envelope(load_json(payload, "韭研公社异动解析")))

        return await self.cache.get(f"jygs:actions:{day.isoformat()}", ttl, load)

    async def check(self, day: date) -> None:
        """用当前会话请求一次「条数」接口；会话无效抛 `UPSTREAM_AUTH`。不走缓存。"""

        if not self.session.strip():
            raise ProviderError(code="NOT_CONFIGURED", message=src.NOT_CONFIGURED, status_code=503)
        stamp, token = src.sign(await self._windows(), self.now())
        headers: Mapping[str, str] = src.api_headers(stamp, token, self.session.strip())
        body = json.dumps({"date": day.isoformat()}).encode()
        payload = await self.gateway.post(src.api_url("action/count-pc"), body, headers)
        src.check_envelope(load_json(payload, "韭研公社登录校验"))


def credentials_from(engine: Engine) -> Callable[[], tuple[str, str]]:
    """运行时读「界面保存的加密配置 > 环境变量」里的 (会话, 到期时间)。"""

    def read() -> tuple[str, str]:
        settings = effective_settings(engine)
        return settings.jygs_session, settings.jygs_session_expires

    return read


def ttl_for(day: date, today: date, live: bool) -> int:
    if day < today:
        return _TTL_PAST
    return _TTL_LIVE if live else _TTL_IDLE


__all__ = [
    "JygsActions",
    "JygsClient",
    "SOURCE_NAME",
    "credentials_from",
    "parse_actions",
    "ttl_for",
]
