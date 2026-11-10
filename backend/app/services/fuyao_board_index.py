"""同花顺扶摇「个股 → 所属板块」反查索引（所属板块回退链的最后一环）。

扶摇只有「板块 → 成分股」接口，所以把概念板块与行业板块的成分全量拉一遍，反查成映射缓存起来。
成本：约 700 次请求、几分钟，所以只在东财 F10 与腾讯都取不到时才触发，并且在后台构建，
构建期间的请求直接返回「正在构建」而不是干等；索引缓存 12 小时（板块成分一天变不了几次）。
API Key 由调用方从环境变量 `FUYAO_API_KEY` 取来传入；本模块不保存、不记录它。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic

from app.providers import stock_boards as sb
from app.providers.errors import ProviderError
from app.services import ashare_calc as calc
from app.services.industry_market import Gateway

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "未配置 FUYAO_API_KEY，同花顺扶摇来源已跳过"
BUILDING = "同花顺扶摇板块索引正在后台构建，稍后刷新即可"
_CONCEPT_TAG = "cn_concept"
_INDUSTRY_TAG = "industry"
_MAX_FAIL_RATIO = 0.2


@dataclass
class FuyaoBoardIndex:
    gateway: Gateway
    api_key: Callable[[], str]
    ttl_seconds: float = 12 * 3600
    concurrency: int = 4
    clock: Callable[[], float] = monotonic
    _index: dict[str, tuple[list[str], list[str]]] | None = field(default=None, repr=False)
    _built_at: float = field(default=0.0, repr=False)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _error: str = field(default="", repr=False)

    def configured(self) -> bool:
        return bool(self.api_key().strip())

    def _fresh(self) -> bool:
        return self._index is not None and self.clock() - self._built_at < self.ttl_seconds

    async def lookup(self, code: str) -> tuple[sb.Boards | None, str]:
        """(板块, 失败原因)。板块为 None 表示这次没有可用索引，原因写进 `meta.unavailable`。"""

        if not self.configured():
            return None, NOT_CONFIGURED
        if not self._fresh():
            self._start()
            if self._index is None:
                return None, self._error or BUILDING
        index = self._index or {}
        industries, concepts = index.get(code, ([], []))
        return sb.build_boards(industries, concepts), ""

    def _start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            await self.build()
            self._error = ""
        except ProviderError as error:
            self._error = f"同花顺扶摇板块索引构建失败：{error.message}"
            logger.warning("fuyao board index build failed: %s", error.message)
        except Exception:
            self._error = "同花顺扶摇板块索引构建出错"
            logger.warning("fuyao board index build crashed", exc_info=True)

    async def _fetch(self, url: str, label: str) -> object:
        payload = await self.gateway.get(url, sb.fuyao_headers(self.api_key().strip()))
        doc = calc.load_json(payload, label)
        failure = sb.fuyao_envelope_error(doc)
        if failure is not None:
            code, message = failure
            if code == "2003":
                raise ProviderError(
                    code="UPSTREAM_AUTH", message="扶摇 API Key 无效或已撤销", status_code=401
                )
            raise ProviderError(
                code="UPSTREAM_ERROR", message=f"{label}：{message}", status_code=502
            )
        return doc

    async def build(self) -> None:
        """全量构建；成功才替换旧索引。目录取不到或成分失败过多都算失败。"""

        concepts = sb.parse_fuyao_catalog(
            await self._fetch(sb.fuyao_catalog_url(_CONCEPT_TAG), "扶摇概念目录")
        )
        industries = sorted(
            sb.parse_fuyao_catalog(
                await self._fetch(sb.fuyao_catalog_url(_INDUSTRY_TAG), "扶摇行业目录")
            )
        )
        if not concepts or not industries:
            raise ProviderError(code="UPSTREAM_EMPTY", message="扶摇板块目录为空", status_code=502)
        hits: dict[str, tuple[list[tuple[int, str]], list[tuple[int, str]]]] = {}
        slots = asyncio.Semaphore(self.concurrency)
        failed = 0

        async def pull(order: int, thscode: str, name: str, is_industry: bool) -> None:
            nonlocal failed
            async with slots:
                try:
                    members = sb.parse_fuyao_members(
                        await self._fetch(sb.fuyao_members_url(thscode), f"扶摇成分 {name}")
                    )
                except ProviderError as error:
                    if error.code == "UPSTREAM_AUTH":
                        raise
                    failed += 1
                    return
            for ticker in members:
                inds, cons = hits.setdefault(ticker, ([], []))
                (inds if is_industry else cons).append((order, name))

        # 行业按 thscode 升序（881 一级在前、884 细分在后），行业名才会「大类在前」；
        # 并发完成的先后不定，所以记下目录序号，最后按序号排。
        jobs = [(code, name, True) for code, name in industries]
        jobs += [(code, name, False) for code, name in concepts]
        await asyncio.gather(*(pull(order, *job) for order, job in enumerate(jobs)))
        if failed > len(jobs) * _MAX_FAIL_RATIO or not hits:
            raise ProviderError(
                code="UPSTREAM_PARTIAL",
                message=f"扶摇成分接口失败 {failed}/{len(jobs)} 个板块",
                status_code=502,
            )
        by_code = {
            ticker: ([n for _, n in sorted(inds)], [n for _, n in sorted(cons)])
            for ticker, (inds, cons) in hits.items()
        }
        self._index = by_code
        self._built_at = self.clock()
        logger.info("fuyao board index built: %d stocks, %d boards failed", len(by_code), failed)
