"""研报元数据服务：行业研报与个股研报，来源是东方财富研报中心的公开列表接口。

只取标题、机构、评级、研究员、日期和原文链接，不取正文、不取盈利预测，也不入库：
研报正文的版权归券商，这里只做「有哪些研报、谁在什么时候怎么评」的索引。
同一个查询缓存一小时，上游失败时有旧值就返回旧值并标 stale。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import TypeVar

from app.providers import eastmoney_boards as em
from app.services.industry_market import Gateway
from app.services.market_cache import Cached, TtlCache, market_cache, shared_gateway

T = TypeVar("T")

_TTL_REPORTS = 3600
SOURCE_PAGE = "https://data.eastmoney.com/report/"


@dataclass
class ResearchReportService:
    gateway: Gateway
    cache: TtlCache = field(default_factory=market_cache)

    async def industry(
        self, code: str, start: date, end: date
    ) -> Cached[tuple[em.IndustryReport, ...]]:
        url = em.reports_url(code, start, end)
        return await self.cache.get(
            f"reports:industry:{code}:{start}:{end}",
            _TTL_REPORTS,
            lambda: self._fetch(url, em.parse_reports),
        )

    async def stock(self, code: str, start: date, end: date) -> Cached[tuple[em.StockReport, ...]]:
        url = em.stock_reports_url(code, start, end)
        return await self.cache.get(
            f"reports:stock:{code}:{start}:{end}",
            _TTL_REPORTS,
            lambda: self._fetch(url, em.parse_stock_reports),
        )

    async def _fetch(self, url: str, parse: Callable[[bytes], T]) -> T:
        return parse(await self.gateway.get(url, em.EASTMONEY_HEADERS))


_service: ResearchReportService | None = None


def get_report_service() -> ResearchReportService:
    global _service
    if _service is None:
        _service = ResearchReportService(gateway=shared_gateway())
    return _service
