"""工具结果的短时缓存（规格 §6）。

进程内 LRU，最多 256 条，存活 5 分钟；键为（工具名、规范化参数、实际时点）。
`watchlist` 是用户自己的数据且随时会改，不缓存；`corpus_search` 的结果含用户私有文档，
也不缓存；`quote_table` 按本回合的来源编号核对，换个回合就不成立，不缓存；
`unavailable` 可能马上就同步好了，也不缓存。
缓存里的值是不可变的 `ToolResult`，所以不同用户、不同回合共用是安全的（缓存的都是公共数据）。
"""

import json
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import date
from typing import Any

from app.services.assistant.results import ToolResult

NEVER_CACHED = frozenset(
    {"watchlist", "corpus_search", "quote_table", "web_search", "news_flash", "fetch_url"}
)
CacheKey = tuple[str, str, str]


def cache_key(tool: str, args: dict[str, Any], as_of: date) -> CacheKey:
    return (
        tool,
        json.dumps(args, sort_keys=True, ensure_ascii=False, default=str),
        as_of.isoformat(),
    )


class ToolCache:
    def __init__(
        self,
        *,
        max_entries: int = 256,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_entries
        self._ttl = ttl_seconds
        self._clock = clock
        self._items: OrderedDict[CacheKey, tuple[float, ToolResult]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: CacheKey) -> ToolResult | None:
        with self._lock:
            hit = self._items.get(key)
            if hit is None:
                return None
            if hit[0] <= self._clock():
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return hit[1]

    def put(self, key: CacheKey, result: ToolResult) -> None:
        if result.tool in NEVER_CACHED or result.status == "unavailable":
            return
        with self._lock:
            self._items[key] = (self._clock() + self._ttl, result)
            self._items.move_to_end(key)
            while len(self._items) > self._max:
                self._items.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
