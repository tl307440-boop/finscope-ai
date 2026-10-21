"""实时财经新闻标题服务：东方财富 7x24 快讯和关键词新闻搜索，只存在进程内存里几分钟。

不落库、不写日志正文：只有标题、时间、媒体名和链接，用来回答「现在市场在讨论什么」。
同一个查询缓存 1–2 分钟，上游失败时有旧值就返回旧值并标 stale。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TypeVar

from app.providers import eastmoney_news as news
from app.services.industry_market import Gateway
from app.services.market_cache import Cached, TtlCache, shared_gateway

T = TypeVar("T")

_TTL_FLASH = 60
_TTL_SEARCH = 120
FLASH_POOL = 100  # 一次取多少条快讯，再按关键词在本地筛


@dataclass
class NewsFlashService:
    gateway: Gateway
    cache: TtlCache = field(default_factory=lambda: TtlCache(failure_backoff_seconds=10.0))

    async def flashes(self) -> Cached[tuple[news.NewsItem, ...]]:
        url = news.flash_url(FLASH_POOL)
        return await self.cache.get(
            "news:flash",
            _TTL_FLASH,
            lambda: self._fetch(url, news.FLASH_HEADERS, news.parse_flash),
        )

    async def search(self, keyword: str) -> Cached[tuple[news.NewsItem, ...]]:
        url = news.search_url(keyword)
        return await self.cache.get(
            f"news:search:{keyword}",
            _TTL_SEARCH,
            lambda: self._fetch(url, news.SEARCH_HEADERS, news.parse_search),
        )

    async def _fetch(self, url: str, headers: dict[str, str], parse: Callable[[bytes], T]) -> T:
        return parse(await self.gateway.get(url, headers))


_service: NewsFlashService | None = None


def get_news_service() -> NewsFlashService:
    global _service
    if _service is None:
        _service = NewsFlashService(gateway=shared_gateway())
    return _service
