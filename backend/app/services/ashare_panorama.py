"""A 股全景看板服务：把东财 / 同花顺 / 财联社 / 金十等公开源整理成前端直接可画的数据。

约定：
- 只用真实数据。某个上游失败，只让对应字段为空，并写进 `meta.unavailable`；永远不造假数据。
- 每个上游请求都走进程内共用的限速出口 `shared_gateway()` 与带「过期仍可用」的缓存 `TtlCache`；
  交易时段内缓存几十秒，非交易时段和历史日缓存更久。
- 数据日期取东财日 K 里最近一个有数据的交易日；交易时段内标记为 `intraday`，收盘后为 `close`。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from time import monotonic
from typing import Any

from app.core.config import get_settings
from app.providers import ashare_panorama as src
from app.providers import eastmoney_boards as em
from app.providers import eastmoney_sentiment as es
from app.providers import tencent_quote as tq
from app.providers.errors import ProviderError
from app.schemas.ashare_panorama import (
    BrokenStreak,
    DailyBar,
    DragonTigerPayload,
    FuturesPayload,
    FuturesRow,
    GlobalQuote,
    GlobalQuotesPayload,
    IntradayPoint,
    JygsActionsPayload,
    JygsSector,
    JygsState,
    JygsStatusPayload,
    JygsStock,
    LadderPayload,
    LadderStock,
    LadderTier,
    LeadingPayload,
    LeadingSector,
    LhbSeat,
    LhbSeatLine,
    LhbStock,
    MarginSeries,
    NewsItem,
    NewsPayload,
    NewsStock,
    PanoramaMeta,
    PopularStock,
    PremiumHistory,
    SentimentPayload,
    Session,
    StockDetailPayload,
    StreakLeader,
    SwingStock,
    VolumeMarginPayload,
    VolumeSeries,
)
from app.services import ashare_calc as calc
from app.services import jygs
from app.services.industry_market import Gateway
from app.services.market_cache import Cached, TtlCache, market_cache, shared_gateway
from app.utils.market_time import SHANGHAI
from app.utils.time import utc_now

_SESSION_OPEN = time(9, 15)
_SESSION_CLOSE = time(15, 5)
_TTL_LIVE = 30
_TTL_IDLE = 600
_TTL_PAST = 6 * 3600
_NOON = "11:30"

_GLOBAL: tuple[tuple[str, str, str, str, str], ...] = (
    ("1.000001", "000001", "000001.SH", "上证指数", "A股宽基"),
    ("0.399001", "399001", "399001.SZ", "深证成指", "A股宽基"),
    ("0.399006", "399006", "399006.SZ", "创业板指", "A股宽基"),
    ("1.000688", "000688", "000688.SH", "科创50", "A股宽基"),
    ("0.899050", "899050", "899050.BJ", "北证50", "A股宽基"),
    ("100.HSI", "HSI", "HSI.HK", "恒生指数", "海外指数"),
    ("100.NDX", "NDX", "NDX.US", "纳斯达克", "海外指数"),
    ("100.SPX", "SPX", "SPX.US", "标普500", "海外指数"),
    ("101.GC00Y", "GC00Y", "COMEX黄金", "黄金", "大宗商品"),
    ("102.CL00Y", "CL00Y", "NYMEX原油", "原油", "大宗商品"),
)
# 成交额用市场口径指数：上证指数≈沪市，深证成指/深证综指≈深市，北证50≈北交所，创业板综、科创综指。
_VOLUME_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("sh", "上证市场", "1.000001"),
    ("sz", "深证市场", "0.399001"),
    ("bj", "北证市场", "0.899050"),
    ("chinext", "创业板", "0.399102"),
    ("star", "科创板", "1.000680"),
)
_FUTURES_NAME = {
    "IF": "IF 沪深300期指",
    "IC": "IC 中证500期指",
    "IM": "IM 中证1000期指",
    "IH": "IH 上证50期指",
    "ALL": "四大期指合并",
}
_KLINE_DAYS = 15
_MAX_SWING_PAGES = 8
_UNAVAILABLE_NEWS = "该来源暂不可用"
_JYGS_WARN_DAYS = 3  # 韭研登录会话还剩几天时开始提醒重新扫码


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class _Acc:
    """一次请求内汇总各上游的状态，最后折成 `PanoramaMeta`。"""

    stale: bool = False
    unavailable: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    fetched: list[datetime] = field(default_factory=list)

    def source(self, name: str) -> None:
        if name not in self.sources:
            self.sources.append(name)

    def meta(self, day: str | None, session: Session) -> PanoramaMeta:
        return PanoramaMeta(
            tradeDate=day,
            session=session,
            fetchedAt=max(self.fetched) if self.fetched else None,
            stale=self.stale,
            sources=self.sources,
            notes=self.notes,
            unavailable=self.unavailable,
        )


@dataclass
class ASharePanoramaService:
    gateway: Gateway
    cache: TtlCache = field(default_factory=market_cache)
    now: Callable[[], datetime] = field(default=utc_now)
    # 韭研公社扫码登录后的 SESSION Cookie（本机 .env 的 JYGS_SESSION）；留空则不接入异动解析。
    jygs_session: str = ""
    jygs_session_expires: str = ""
    # 运行时取 (会话, 到期时间)：生产里读「界面保存的加密配置 > 环境变量」，所以扫码登录后立刻生效。
    jygs_credentials: Callable[[], tuple[str, str]] | None = None
    _jygs_checked: tuple[float, str] | None = field(default=None, repr=False)

    # ───────────── 基础：取数 / 交易日 ─────────────

    def _local(self) -> datetime:
        return self.now().astimezone(SHANGHAI)

    def _live(self) -> bool:
        local = self._local()
        return local.weekday() < 5 and _SESSION_OPEN <= local.time() <= _SESSION_CLOSE

    def _ttl(self, day: date | str | None = None) -> int:
        if day is not None:
            current = day if isinstance(day, date) else date.fromisoformat(day)
            if current < self._local().date():
                return _TTL_PAST
        return _TTL_LIVE if self._live() else _TTL_IDLE

    async def _doc(
        self,
        acc: _Acc,
        label: str,
        url: str,
        headers: Mapping[str, str],
        *,
        ttl: int | None = None,
        source: str | None = None,
        body: bytes | None = None,
    ) -> Any | None:
        """取一个 JSON 文档；失败只记 `unavailable`，返回 None。"""

        key = f"ashare:{label}:{url}" if body is None else f"ashare:{label}:{url}:{body!r}"

        async def load() -> Any:
            if body is None:
                payload = await self.gateway.get(url, headers)
            else:
                payload = await self.gateway.post(url, body, headers)
            return calc.load_json(payload, label)

        try:
            cached: Cached[Any] = await self.cache.get(key, ttl or self._ttl(), load)
        except ProviderError as error:
            acc.unavailable[label] = error.message
            return None
        acc.fetched.append(cached.fetched_at)
        acc.stale = acc.stale or cached.stale
        if source:
            acc.source(source)
        return cached.value

    async def _days(self, acc: _Acc) -> list[str]:
        """最近交易日列表（升序）：取自上证指数日 K，盘中最后一天就是今天。

        先用腾讯日 K（生产服务器连不上东财 push2his），失败再试东财。
        """

        code = "sh000001"

        async def load_days() -> list[str]:
            payload = await self.gateway.get(tq.kline_url(code, _KLINE_DAYS), tq.HEADERS)
            return tq.parse_kline_days(payload, code)

        tencent_days: list[str] | None = await self._tencent(
            acc, "交易日历", 300, load_days, "腾讯财经 日K"
        )
        if tencent_days:
            return tencent_days
        doc = await self._doc(
            acc,
            "交易日历",
            src.daily_kline_url("1.000001", _KLINE_DAYS),
            src.EM_HEADERS,
            ttl=300,
            source="东方财富 日K",
        )
        return [bar.day for bar in calc.parse_klines(calc.as_doc(doc))] if doc else []

    async def _resolve(self, acc: _Acc) -> tuple[date | None, Session, list[str]]:
        days = await self._days(acc)
        if not days:
            return None, "unknown", []
        latest = date.fromisoformat(days[-1])
        local = self._local()
        intraday = latest == local.date() and self._live()
        return latest, "intraday" if intraday else "close", days

    async def _pool(
        self, acc: _Acc, kind: es.PoolKind, day: date, label: str
    ) -> list[calc.PoolRow] | None:
        doc = await self._doc(
            acc,
            label,
            es.pool_url(kind, day),
            src.EM_HEADERS,
            ttl=self._ttl(day),
            source="东方财富 涨停板行情",
        )
        return None if doc is None else calc.parse_today_pool(doc)

    async def _yesterday(self, acc: _Acc, day: date) -> list[calc.YesterdayRow] | None:
        doc = await self._doc(
            acc,
            "昨日涨停池",
            src.yesterday_pool_url(day),
            src.EM_HEADERS,
            ttl=self._ttl(day),
            source="东方财富 涨停板行情",
        )
        return None if doc is None else calc.parse_yesterday_pool(doc)

    async def _ths_pool(self, acc: _Acc, day: date) -> dict[str, calc.ThsReason]:
        merged: dict[str, calc.ThsReason] = {}
        first = await self._doc(
            acc,
            "同花顺涨停池",
            src.ths_pool_url(day, 1),
            src.THS_DATA_HEADERS,
            ttl=self._ttl(day),
            source="同花顺 涨停原因",
        )
        if first is None:
            return merged
        rows, pages = calc.parse_ths_pool(calc.as_doc(first))
        merged.update(rows)
        for page in range(2, min(pages, 4) + 1):
            more = await self._doc(
                acc,
                "同花顺涨停池",
                src.ths_pool_url(day, page),
                src.THS_DATA_HEADERS,
                ttl=self._ttl(day),
            )
            if more is not None:
                merged.update(calc.parse_ths_pool(calc.as_doc(more))[0])
        return merged

    async def _creds(self) -> tuple[str, str]:
        if self.jygs_credentials is None:
            return self.jygs_session.strip(), self.jygs_session_expires.strip()
        session, expires = await asyncio.to_thread(self.jygs_credentials)
        return session.strip(), expires.strip()

    def jygs_reset(self) -> None:
        """重新登录后丢掉「会话是否有效」的短期记忆。"""

        self._jygs_checked = None

    async def jygs_status(self) -> JygsStatusPayload:
        """会话状态：没登录 / 有效 / 快到期 / 已失效。只返回状态，不返回会话值。"""

        session, expires_text = await self._creds()
        if not session:
            return JygsStatusPayload(
                state="not_configured", message="还没有登录韭研公社，异动解析暂不可用"
            )
        expires = _parse_iso(expires_text)
        days_left = None if expires is None else max(0, (expires - self.now()).days)
        expires_at = expires.isoformat() if expires else None

        def status(state: JygsState, message: str) -> JygsStatusPayload:
            return JygsStatusPayload(
                state=state, message=message, expiresAt=expires_at, daysLeft=days_left
            )

        if expires is not None and expires <= self.now():
            return status("expired", "韭研登录会话已过期，请重新扫码登录")
        checked = self._jygs_checked
        if checked is None or checked[0] <= monotonic():
            verdict = await self._probe_jygs(session)
            self._jygs_checked = (monotonic() + 300, verdict)
        else:
            verdict = checked[1]
        if verdict == "invalid":
            return status("expired", "韭研登录会话已失效，请重新扫码登录")
        if verdict == "unknown":
            return status("unknown", "暂时无法确认韭研登录是否有效")
        if days_left is not None and days_left <= _JYGS_WARN_DAYS:
            return status("expiring", f"韭研登录会话还剩 {days_left} 天到期，建议提前重新扫码登录")
        return status("ok", "韭研公社已登录")

    async def _probe_jygs(self, session: str) -> str:
        client = jygs.JygsClient(self.gateway, self.cache, session, self.now)
        try:
            await client.check(self._local().date())
        except ProviderError as error:
            return "invalid" if error.code == "UPSTREAM_AUTH" else "unknown"
        return "valid"

    async def _jygs(self, acc: _Acc, day: date) -> jygs.JygsActions | None:
        """韭研公社异动解析；没配登录态只记一条说明，失败 / 失效记进 `unavailable`。"""

        label = "韭研公社异动解析"
        session, _ = await self._creds()
        if not session:
            note = "未登录韭研公社，未接入异动解析（在连板分析页的韭研面板点「扫码登录」）。"
            if note not in acc.notes:
                acc.notes.append(note)
            return None
        client = jygs.JygsClient(self.gateway, self.cache, session, self.now)
        ttl = jygs.ttl_for(day, self._local().date(), self._live())
        try:
            cached = await client.actions(day, ttl)
        except ProviderError as error:
            acc.unavailable[label] = error.message
            return None
        acc.fetched.append(cached.fetched_at)
        acc.stale = acc.stale or cached.stale
        acc.source(jygs.SOURCE_NAME)
        return cached.value

    async def _tencent(
        self,
        acc: _Acc,
        label: str,
        ttl: int,
        load: Callable[[], Awaitable[Any]],
        source: str,
    ) -> Any | None:
        """腾讯行情的备用通道：成功返回数据并记来源，失败返回 None（不写 `unavailable`）。"""

        try:
            cached: Cached[Any] = await self.cache.get(f"ashare:tencent:{label}", ttl, load)
        except ProviderError:
            return None
        acc.fetched.append(cached.fetched_at)
        acc.stale = acc.stale or cached.stale
        acc.source(source)
        return cached.value

    async def _tencent_quotes(self, acc: _Acc, secids: list[str]) -> dict[str, calc.Doc]:
        """用腾讯报价补东财 ulist 取不到的行（键名、键格式与 `parse_ulist` 一致）。"""

        codes = {code: secid for secid in secids if (code := tq.tencent_code(secid))}
        if not codes:
            return {}
        ordered = sorted(codes)

        async def load() -> dict[str, tq.Quote]:
            return tq.parse_quotes(await self.gateway.get(tq.quote_url(ordered), tq.HEADERS))

        quotes = await self._tencent(
            acc, f"报价:{','.join(ordered)}", self._ttl(), load, "腾讯财经 行情"
        )
        if not quotes:
            return {}
        return {
            codes[code]: tq.as_em_row(quote, codes[code])
            for code, quote in quotes.items()
            if code in codes
        }

    async def _quotes(
        self, acc: _Acc, label: str, secids: list[str], fields: str
    ) -> dict[str, calc.Doc]:
        out: dict[str, calc.Doc] = {}
        unique = list(dict.fromkeys(secids))
        for start in range(0, len(unique), 80):
            batch = unique[start : start + 80]
            doc = await self._doc(
                acc,
                label,
                src.ulist_url(batch, fields),
                src.EM_HEADERS,
                source="东方财富 行情",
            )
            if doc is not None:
                out.update(calc.parse_ulist(calc.as_doc(doc)))
                continue
            # 东财 push2 在部分机房连不上：改用腾讯报价；腾讯也取不到时保留东财的失败原因。
            fallback = await self._tencent_quotes(acc, batch)
            if fallback:
                acc.unavailable.pop(label, None)
                out.update(fallback)
        return out

    # ───────────── 1. 全球核心指数 ─────────────

    async def global_quotes(self) -> GlobalQuotesPayload:
        acc = _Acc()
        secids = [row[0] for row in _GLOBAL]
        quotes, *trend_docs = await asyncio.gather(
            self._quotes(acc, "全球行情", secids, "f2,f3,f4,f6,f12,f13,f14"),
            *(
                self._doc(
                    acc,
                    f"分时 {row[3]}",
                    src.trends_url(row[0]),
                    src.EM_HEADERS,
                    source="东方财富 分时",
                )
                for row in _GLOBAL
            ),
        )
        items: list[GlobalQuote] = []
        for (secid, code, symbol, name, category), trend_doc in zip(
            _GLOBAL, trend_docs, strict=True
        ):
            row = quotes.get(secid, {})
            spark: list[float] = []
            if trend_doc is not None:
                latest = calc.latest_day_rows(calc.parse_trends(calc.as_doc(trend_doc)))
                spark = [round(p.price, 4) for p in latest]
                spark = calc.downsample(spark, 40)
            items.append(
                GlobalQuote(
                    code=code,
                    symbol=symbol,
                    name=name,
                    category=category,  # type: ignore[arg-type]
                    price=calc.num(row.get("f2")),
                    change=calc.num(row.get("f4")),
                    changePct=calc.num(row.get("f3")),
                    amountYuan=calc.num(row.get("f6")),
                    spark=spark,
                )
            )
        if not quotes:
            acc.unavailable.setdefault("全球行情", _UNAVAILABLE_NEWS)
        else:
            for item in items:
                if item.price is None:
                    acc.unavailable.setdefault(item.name, "该来源暂无行情")
        return GlobalQuotesPayload(
            quotes=items,
            meta=acc.meta(
                self._local().date().isoformat(), "intraday" if self._live() else "close"
            ),
        )

    # ───────────── 2. 短线情绪 ─────────────

    async def sentiment(self) -> SentimentPayload:
        acc = _Acc()
        day, session, days = await self._resolve(acc)
        if day is None:
            return SentimentPayload(meta=acc.meta(None, "unknown"))
        previous = date.fromisoformat(days[-2]) if len(days) >= 2 else None
        acc.notes += [
            "涨跌家数来自东财涨跌分布；涨跌幅中位数按全市场涨幅排名取中间值（偶数只取中间两只均值）。",
            "自然涨停/跌停：剔除 ST 与带 N/C 前缀的新股；炸板率 = 炸板 /（涨停 + 炸板）。",
            "晋级率 = 昨日涨停今日继续涨停 / 昨日涨停；"
            "溢价率 = 昨日涨停（连板、炸板）股今日平均涨跌幅。",
            "冲高回落 = 日内最高涨幅 − 收盘涨幅 ≥ 10 个百分点；"
            "探底回升 = 收盘涨幅 − 日内最低涨幅 ≥ 10 个百分点。",
        ]
        (zt, zb, dt), (fenbu_doc, yst, ths, swings) = await asyncio.gather(
            _all3(
                self._pool(acc, "limit_up", day, "涨停池"),
                self._pool(acc, "broken", day, "炸板池"),
                self._pool(acc, "limit_down", day, "跌停池"),
            ),
            _all4(
                self._doc(
                    acc, "涨跌分布", es.fenbu_url(), src.EM_HEADERS, source="东方财富 涨跌分布"
                ),
                self._yesterday(acc, day),
                self._ths_pool(acc, day),
                self._swings(acc),
            ),
        )
        breadth: calc.Breadth | None = None
        median: float | None = None
        if fenbu_doc is not None:
            buckets = es.parse_fenbu(_to_bytes(fenbu_doc)).buckets
            breadth = calc.breadth_from_buckets(buckets)
            median = await self._median(acc, breadth.up + breadth.down + breadth.flat)
        zt_rows = zt or []
        zb_count = None if zb is None else len(zb)
        zt_count = None if zt is None else len(zt_rows)
        broken_rate = (
            None
            if zb_count is None or zt_count is None or zt_count + zb_count == 0
            else round(zb_count / (zt_count + zb_count) * 100, 2)
        )
        promo = calc.promotion(zt_rows, yst or []) if yst is not None and zt is not None else None
        leader = self._leader(zt_rows, ths)
        broken_premium = await self._broken_premium(acc, previous)
        history = await self._premium_history(acc, days[-5:])
        sectors = {r.code: r.industry for r in zt_rows}
        return SentimentPayload(
            tradeDate=day.isoformat(),
            upCount=None if breadth is None else breadth.up,
            downCount=None if breadth is None else breadth.down,
            flatCount=None if breadth is None else breadth.flat,
            medianChangePct=median,
            limitUpTotal=zt_count,
            limitDownTotal=None if dt is None else len(dt),
            naturalLimitUp=None
            if zt is None
            else calc.natural_limit_count(r.name for r in zt_rows),
            naturalLimitDown=None if dt is None else calc.natural_limit_count(r.name for r in dt),
            brokenCount=zb_count,
            brokenRatePct=broken_rate,
            streakCount=None if zt is None else sum(1 for r in zt_rows if r.streak >= 2),
            promotionRatePct=None if promo is None else promo.overall_pct,
            maxStreakHeight=None if zt is None else max((r.streak for r in zt_rows), default=0),
            maxStreakLeader=leader,
            pullbackOver10Count=None if swings is None else len(calc.pullbacks(swings)),
            reboundOver10Count=None if swings is None else len(calc.rebounds(swings)),
            pullbackStocks=[
                self._swing(s, ths, sectors, up=False) for s in calc.pullbacks(swings or [])[:30]
            ],
            reboundStocks=[
                self._swing(s, ths, sectors, up=True) for s in calc.rebounds(swings or [])[:30]
            ],
            yesterdayLimitUpPremiumPct=None if yst is None else calc.premium(yst),
            yesterdayStreakPremiumPct=None if yst is None else calc.premium(yst, min_streak=2),
            yesterdayBrokenPremiumPct=broken_premium,
            premiumHistory=history,
            meta=acc.meta(day.isoformat(), session),
        )

    async def _median(self, acc: _Acc, active: int) -> float | None:
        values: list[float | None] = []
        for page, index in calc.median_rank_pages(active):
            doc = await self._doc(
                acc,
                "涨幅排名",
                src.clist_url(src.AShareFs, "f3", page, "f3,f12"),
                src.EM_HEADERS,
                source="东方财富 行情",
            )
            rows = calc.as_list(calc.as_doc(calc.as_doc(doc).get("data")).get("diff"))
            if index < len(rows):
                values.append(calc.num(calc.as_doc(rows[index]).get("f3")))
        return calc.mean_of(values)

    async def _swings(self, acc: _Acc) -> list[calc.Swing] | None:
        rows: list[calc.Doc] = []
        for page in range(1, _MAX_SWING_PAGES + 1):
            doc = await self._doc(
                acc,
                "振幅榜",
                src.clist_url(src.AShareFs, "f7", page, "f2,f3,f7,f12,f14,f15,f16,f18,f100"),
                src.EM_HEADERS,
                source="东方财富 行情",
            )
            if doc is None:
                return None if not rows else calc.swing_rows(rows)
            batch = [
                calc.as_doc(r)
                for r in calc.as_list(calc.as_doc(calc.as_doc(doc).get("data")).get("diff"))
            ]
            rows += batch
            tail = calc.num(batch[-1].get("f7")) if batch else None
            if len(batch) < src.PAGE_SIZE or tail is None or tail < 10:
                break
        return calc.swing_rows(rows)

    async def _broken_premium(self, acc: _Acc, previous: date | None) -> float | None:
        if previous is None:
            return None
        pool = await self._pool(acc, "broken", previous, "昨日炸板池")
        if not pool:
            return None
        quotes = await self._quotes(
            acc, "昨日炸板今日行情", [calc.secid_for(r.code) for r in pool], "f2,f3,f12,f13,f14"
        )
        changes = [calc.num(q.get("f3")) for q in quotes.values()]
        return calc.mean_of(changes)

    async def _premium_history(self, acc: _Acc, days: list[str]) -> PremiumHistory:
        history = PremiumHistory()
        results = await asyncio.gather(*(self._yesterday(acc, date.fromisoformat(d)) for d in days))
        for label, rows in zip(days, results, strict=True):
            if not rows:
                continue
            limit_up, streak = calc.premium(rows), calc.premium(rows, min_streak=2)
            if limit_up is None:
                continue
            history.dates.append(label[5:])
            history.limitUpPremium.append(limit_up)
            history.streakPremium.append(streak if streak is not None else 0.0)
        return history

    @staticmethod
    def _leader(pool: list[calc.PoolRow], ths: Mapping[str, calc.ThsReason]) -> StreakLeader | None:
        if not pool:
            return None
        top = min(pool, key=lambda r: (-r.streak, r.first_seal or "99"))
        reason = ths.get(top.code)
        return StreakLeader(
            code=calc.suffix_code(top.code),
            name=top.name,
            changePct=top.change_pct,
            sector=top.industry,
            reason=reason.reason if reason else "",
        )

    @staticmethod
    def _swing(
        item: calc.Swing, ths: Mapping[str, calc.ThsReason], sectors: Mapping[str, str], *, up: bool
    ) -> SwingStock:
        reason = ths.get(item.code)
        return SwingStock(
            code=calc.suffix_code(item.code),
            name=item.name,
            price=item.price,
            changePct=round(item.change_pct, 2),
            swingPct=round(
                item.change_pct - item.trough_pct if up else item.change_pct - item.peak_pct, 2
            ),
            peakOrTroughPct=round(item.trough_pct if up else item.peak_pct, 2),
            sector=item.sector or sectors.get(item.code, ""),
            reason=reason.reason if reason else "",
        )

    # ───────────── 3. 成交量能与两融 ─────────────

    async def volume_margin(self) -> VolumeMarginPayload:
        acc = _Acc()
        day, session, days = await self._resolve(acc)
        acc.notes += [
            "成交额取市场口径指数：上证指数≈沪市，深证成指≈深市，北证50≈北交所，创业板综、科创综指；"
            "全市场 = 沪 + 深 + 京。",
            "午盘（11:30）成交额取分时成交额累计；对比的是上一个交易日同一时点。",
            "两融余额「日变动」是相邻交易日的余额差，不是融资净买入。",
        ]
        klines = await asyncio.gather(
            *(
                self._doc(
                    acc,
                    f"日K {name}",
                    src.daily_kline_url(secid, 9),
                    src.EM_HEADERS,
                    source="东方财富 日K",
                )
                for _, name, secid in _VOLUME_INDEXES
            )
        )
        trends = await asyncio.gather(
            *(
                self._doc(
                    acc,
                    f"分时 {name}",
                    src.trends_url(secid, 2),
                    src.EM_HEADERS,
                    source="东方财富 分时",
                )
                for _, name, secid in _VOLUME_INDEXES
            )
        )
        amounts: dict[str, dict[str, float]] = {}
        noon_today: dict[str, float | None] = {}
        noon_prev: dict[str, float | None] = {}
        for (key, _, _), k_doc, t_doc in zip(_VOLUME_INDEXES, klines, trends, strict=True):
            if k_doc is not None:
                amounts[key] = {b.day: b.amount for b in calc.parse_klines(calc.as_doc(k_doc))}
            if t_doc is not None:
                rows = calc.parse_trends(calc.as_doc(t_doc)).rows
                dates = sorted({r.day for r in rows})
                if dates:
                    today = dates[-1]
                    noon_today[key] = (
                        calc.amount_until(rows, today, _NOON)
                        if session != "intraday" or self._local().strftime("%H:%M") >= _NOON
                        else None
                    )
                    noon_prev[key] = (
                        calc.amount_until(rows, dates[-2], _NOON) if len(dates) >= 2 else None
                    )
        # 东财日 K / 分时连不上时，改用腾讯分时的「当日累计成交额」：只有今天，没有历史日、
        # 没有昨日同一时点，对应字段保持为空。
        fallback_used = False
        for key, name, secid in _VOLUME_INDEXES:
            if key in amounts or key in noon_today:
                continue
            minutes = await self._tencent_minutes(acc, secid)
            if minutes is not None and minutes.total_amount is not None:
                amounts[key] = {minutes.day: minutes.total_amount}
                noon_today[key] = (
                    minutes.amount_until(_NOON)
                    if session != "intraday" or self._local().strftime("%H:%M") >= _NOON
                    else None
                )
            else:
                # 腾讯分时不覆盖北交所：退一步取报价里的当日成交额（没有午盘累计）。
                quote = (await self._tencent_quotes(acc, [secid])).get(secid, {})
                total = calc.num(quote.get("f6"))
                if day is None or total is None:
                    continue
                amounts[key] = {day.isoformat(): total}
            fallback_used = True
            acc.unavailable.pop(f"日K {name}", None)
            acc.unavailable.pop(f"分时 {name}", None)
        if fallback_used:
            acc.notes.append(
                "东方财富日 K / 分时在本服务器连不上，成交额改用腾讯分时的当日累计；"
                "历史日成交额和「昨日同一时点」对比暂缺。"
            )
        volumes = self._volume_series(amounts, noon_today, noon_prev, days[-7:])
        margins = await self._margins(acc)
        return VolumeMarginPayload(
            volumes=volumes,
            margins=margins,
            meta=acc.meta(day.isoformat() if day else None, session),
        )

    async def _tencent_minutes(self, acc: _Acc, secid: str) -> tq.Minutes | None:
        code = tq.tencent_code(secid)
        if code is None:
            return None

        async def load() -> tq.Minutes:
            return tq.parse_minutes(await self.gateway.get(tq.minute_url(code), tq.HEADERS), code)

        minutes: tq.Minutes | None = await self._tencent(
            acc, f"分时:{code}", self._ttl(), load, "腾讯财经 分时"
        )
        return minutes

    @staticmethod
    def _volume_series(
        amounts: Mapping[str, Mapping[str, float]],
        noon_today: Mapping[str, float | None],
        noon_prev: Mapping[str, float | None],
        window: list[str],
    ) -> list[VolumeSeries]:
        def build(key: str, label: str, parts: tuple[str, ...]) -> VolumeSeries | None:
            if any(p not in amounts for p in parts):
                return None
            dates = [d for d in window if all(d in amounts[p] for p in parts)]
            history = [round(sum(amounts[p][d] for p in parts) / 1e8, 2) for d in dates]
            nt = [noon_today.get(p) for p in parts]
            npv = [noon_prev.get(p) for p in parts]
            noon = (
                None
                if any(v is None for v in nt)
                else round(sum(v for v in nt if v is not None) / 1e8, 2)
            )
            prev = (
                None
                if any(v is None for v in npv)
                else round(sum(v for v in npv if v is not None) / 1e8, 2)
            )
            delta = None if noon is None or prev is None else round(noon - prev, 2)
            delta_pct = None if delta is None or not prev else round(delta / prev * 100, 2)
            return VolumeSeries(
                key=key,  # type: ignore[arg-type]
                label=label,
                todayAmountYi=history[-1] if history else None,
                noonAmountYi=noon,
                noonDeltaYi=delta,
                noonDeltaPct=delta_pct,
                historyDates=[d[5:] for d in dates],
                historyAmountYi=history,
            )

        series = [
            build("all", "全市场", ("sh", "sz", "bj")),
            build("sh", "上证市场", ("sh",)),
            build("sz", "深证市场", ("sz",)),
            build("bj", "北证市场", ("bj",)),
            build("chinext", "创业板", ("chinext",)),
            build("star", "科创板", ("star",)),
        ]
        return [s for s in series if s is not None]

    async def _margins(self, acc: _Acc) -> list[MarginSeries]:
        doc = await self._doc(
            acc, "两融", src.margin_url(8), src.EM_HEADERS, ttl=900, source="东方财富 数据中心 两融"
        )
        if doc is None:
            return []
        days = calc.parse_margin(calc.as_doc(doc))
        if not days:
            return []
        out: list[MarginSeries] = []
        for key, label, prefix in (
            ("total", "全市场两融", "TOTAL_"),
            ("sh", "沪市两融", "H_"),
            ("sz", "深市两融", "S_"),
            ("bj", "京市（北交所）两融", "B_"),
        ):
            balance = [d.values.get(f"{prefix}RZRQYE") for d in days]
            valid = [(d.day, v) for d, v in zip(days, balance, strict=True) if v is not None]
            if not valid:
                continue
            totals = [round(v / 1e8, 2) for _, v in valid]
            changes = [0.0] + [round(totals[i] - totals[i - 1], 2) for i in range(1, len(totals))]
            latest = days[-1].values
            out.append(
                MarginSeries(
                    key=key,  # type: ignore[arg-type]
                    label=label,
                    tradeDate=valid[-1][0],
                    totalBalanceYi=totals[-1],
                    dailyChangeYi=changes[-1]
                    if len(totals) > 1
                    else calc.to_yi(latest.get(f"{prefix}RZRQYECZ")),
                    rzyeYi=calc.to_yi(latest.get(f"{prefix}RZYE")),
                    rqyeYi=calc.to_yi(latest.get(f"{prefix}RQYE")),
                    rzmreYi=calc.to_yi(latest.get(f"{prefix}RZMRE")),
                    historyDates=[d[5:] for d, _ in valid],
                    historyTotalYi=totals,
                    historyChangeYi=changes,
                )
            )
        return out

    # ───────────── 4. 股指期货机构持仓 ─────────────

    async def futures(self) -> FuturesPayload:
        acc = _Acc()
        acc.notes.append(
            "中金所每个合约只公布持买单量前 20 名的席位：这里是各月份合约「榜内席位」的合并持仓，"
            "不是品种全市场持仓。"
        )
        latest = await self._doc(
            acc,
            "期指最新日",
            src.futures_latest_url(),
            src.EM_HEADERS,
            ttl=900,
            source="东方财富 数据中心 期指持仓",
        )
        rows_doc = calc.as_list(calc.as_doc(calc.as_doc(latest).get("result")).get("data"))
        if not rows_doc:
            return FuturesPayload(rows=[], meta=acc.meta(None, "close"))
        day = date.fromisoformat(calc.text(calc.as_doc(rows_doc[0]).get("TRADE_DATE"))[:10])
        raw: list[calc.Doc] = []
        for page in range(1, 4):
            doc = await self._doc(
                acc,
                "期指席位",
                src.futures_url(day, page),
                src.EM_HEADERS,
                ttl=900,
                source="东方财富 数据中心 期指持仓",
            )
            data = [
                calc.as_doc(r)
                for r in calc.as_list(calc.as_doc(calc.as_doc(doc).get("result")).get("data"))
            ]
            raw += data
            if len(data) < 500:
                break
        agg = calc.aggregate_futures(raw)
        rows: list[FuturesRow] = []
        for product in ("IF", "IC", "IM", "IH"):
            rows += self._futures_rows(product, {m: a for (p, m), a in agg.items() if p == product})
        total: dict[str, calc.FuturesAgg] = {}
        for (_, member), item in agg.items():
            cell = total.setdefault(member, calc.FuturesAgg())
            cell.long_pos += item.long_pos
            cell.long_change += item.long_change
            cell.short_pos += item.short_pos
            cell.short_change += item.short_change
        rows = self._futures_rows("ALL", total, limit=12) + rows
        return FuturesPayload(rows=rows, meta=acc.meta(day.isoformat(), "close"))

    @staticmethod
    def _futures_rows(
        product: str, members: Mapping[str, calc.FuturesAgg], limit: int = 10
    ) -> list[FuturesRow]:
        ordered = sorted(members.items(), key=lambda kv: kv[1].long_pos, reverse=True)
        keys = [kv for kv in ordered if calc.is_key_member(kv[0])]
        others = [kv for kv in ordered if not calc.is_key_member(kv[0])][
            : max(0, limit - len(keys))
        ]
        return [
            FuturesRow(
                contractType=product,  # type: ignore[arg-type]
                contractName=_FUTURES_NAME[product],
                institution=member,
                isKeyInstitution=calc.is_key_member(member),
                longPos=a.long_pos,
                longChange=a.long_change,
                shortPos=a.short_pos,
                shortChange=a.short_change,
                netPos=a.long_pos - a.short_pos,
                netChange=a.long_change - a.short_change,
            )
            for member, a in keys + others
        ]

    # ───────────── 5. 领涨板块与人气股 ─────────────

    async def leading(self) -> LeadingPayload:
        acc = _Acc()
        day, session, _ = await self._resolve(acc)
        blocks_t = (
            self._doc(
                acc,
                "同花顺板块涨停",
                src.ths_block_url(day) if day else "",
                src.THS_DATA_HEADERS,
                source="同花顺 板块涨停",
            )
            if day
            else _none()
        )
        flow_t = self._doc(
            acc,
            "概念板块资金",
            src.clist_url(src.CONCEPT_BOARD_FS, "f3", 1, "f12,f14,f3,f62"),
            src.EM_HEADERS,
            source="东方财富 板块行情",
        )
        hot_t = self._doc(
            acc,
            "东财人气榜",
            em.EM_HOT_RANK_URL,
            src.EM_HEADERS,
            ttl=60,
            source="东方财富 人气榜",
            body=em.hot_rank_body(30),
        )
        blocks_doc, flow_doc, hot_doc = await asyncio.gather(blocks_t, flow_t, hot_t)
        flows = {
            calc.text(calc.as_doc(r).get("f14")): calc.to_yi(calc.as_doc(r).get("f62"))
            for r in calc.as_list(calc.as_doc(calc.as_doc(flow_doc).get("data")).get("diff"))
        }
        sectors: list[LeadingSector] = []
        if blocks_doc is not None:
            blocks = calc.parse_ths_blocks(calc.as_doc(blocks_doc))
            strong = [b for b in blocks if b.limit_up_num >= 3] or [
                b for b in blocks if b.limit_up_num >= 1
            ]
            for b in sorted(
                strong,
                key=lambda x: x.change_pct if x.change_pct is not None else -99,
                reverse=True,
            )[:8]:
                sectors.append(
                    LeadingSector(
                        code=b.code,
                        name=b.name,
                        changePct=b.change_pct,
                        limitUpCount=b.limit_up_num,
                        netInflowYi=flows.get(b.name),
                        leaderCode=calc.suffix_code(b.leader_code) if b.leader_code else "",
                        leaderName=b.leader_name,
                        leaderChangePct=b.leader_change,
                        leaderStreak=b.leader_label,
                    )
                )
        popular = await self._popular(acc, day, hot_doc)
        return LeadingPayload(
            leadingSectors=sectors,
            popularStocks=popular,
            meta=acc.meta(day.isoformat() if day else None, session),
        )

    async def _popular(
        self, acc: _Acc, day: date | None, hot_doc: Any | None
    ) -> list[PopularStock]:
        if hot_doc is None:
            return []
        entries = em.parse_hot_rank(_to_bytes(hot_doc))[:20]
        quotes = await self._quotes(
            acc,
            "人气股行情",
            [em.stock_secid(e.exchange, e.code) for e in entries],
            "f2,f3,f8,f12,f13,f14,f21,f100",
        )
        zt = (
            {r.code: r for r in (await self._pool(acc, "limit_up", day, "涨停池") or [])}
            if day
            else {}
        )
        ths = await self._ths_pool(acc, day) if day else {}
        out: list[PopularStock] = []
        for entry in entries:
            quote = quotes.get(em.stock_secid(entry.exchange, entry.code))
            if quote is None:
                continue
            pool = zt.get(entry.code)
            reason = ths.get(entry.code)
            out.append(
                PopularStock(
                    rank=entry.rank,
                    code=calc.suffix_code(entry.code),
                    name=calc.text(quote.get("f14")),
                    price=calc.num(quote.get("f2")),
                    changePct=calc.num(quote.get("f3")),
                    turnoverPct=calc.num(quote.get("f8")),
                    floatCapYi=calc.to_yi(quote.get("f21")),
                    sealAmountWan=None
                    if pool is None or pool.seal_fund is None
                    else calc.to_wan(pool.seal_fund),
                    streakText=calc.board_label(pool.streak) if pool else "",
                    sector=calc.text(quote.get("f100")),
                    catalyst=reason.reason if reason else "",
                )
            )
        return out

    # ───────────── 6. 龙虎榜 ─────────────

    async def dragon_tiger(self, requested: date | None = None) -> DragonTigerPayload:
        acc = _Acc()
        acc.notes.append(
            "龙虎榜在收盘后才公布，盘中看到的是最近一个已公布的交易日。买卖席位各取每只股票前 5。"
        )
        day = requested
        if day is None:
            doc = await self._doc(
                acc,
                "龙虎榜最新日",
                src.lhb_latest_url(),
                src.EM_HEADERS,
                ttl=300,
                source="东方财富 数据中心 龙虎榜",
            )
            rows = calc.as_list(calc.as_doc(calc.as_doc(doc).get("result")).get("data"))
            if not rows:
                return DragonTigerPayload(stocks=[], seats=[], meta=acc.meta(None, "close"))
            day = date.fromisoformat(calc.text(calc.as_doc(rows[0]).get("TRADE_DATE"))[:10])
        ttl = self._ttl(day) if day < self._local().date() else 300
        stocks_raw, buy_raw, sell_raw = await asyncio.gather(
            self._lhb_rows(acc, "stocks", day, ttl),
            self._lhb_rows(acc, "buy", day, ttl),
            self._lhb_rows(acc, "sell", day, ttl),
        )
        stocks: dict[str, LhbStock] = {}
        for row in stocks_raw:
            code = calc.text(row.get("SECURITY_CODE"))
            if not code:
                continue
            reason = calc.text(row.get("EXPLANATION"))
            if code in stocks:
                prior = stocks[code]
                if reason and reason not in prior.reason:
                    prior.reason = f"{prior.reason}；{reason}" if prior.reason else reason
                continue
            stocks[code] = LhbStock(
                code=calc.suffix_code(code),
                name=calc.text(row.get("SECURITY_NAME_ABBR")),
                closePrice=calc.num(row.get("CLOSE_PRICE")),
                changePct=calc.rnd(calc.num(row.get("CHANGE_RATE"))),
                turnoverPct=calc.rnd(calc.num(row.get("TURNOVERRATE"))),
                floatCapYi=calc.to_yi(row.get("FREE_MARKET_CAP")),
                buyWan=calc.to_wan(row.get("BILLBOARD_BUY_AMT")) or 0.0,
                sellWan=calc.to_wan(row.get("BILLBOARD_SELL_AMT")) or 0.0,
                netWan=calc.to_wan(row.get("BILLBOARD_NET_AMT")) or 0.0,
                reason=reason,
            )
        names = {k: v.name for k, v in stocks.items()}
        seats: list[LhbSeat] = []
        seen: set[tuple[str, str, str, float, float]] = set()
        for side, rows in (("buy", buy_raw), ("sell", sell_raw)):
            for row in rows:
                code, branch = (
                    calc.text(row.get("SECURITY_CODE")),
                    calc.text(row.get("OPERATEDEPT_NAME")),
                )
                buy, sell = calc.to_wan(row.get("BUY")) or 0.0, calc.to_wan(row.get("SELL")) or 0.0
                marker = (code, branch, side, buy, sell)
                if not code or not branch or marker in seen:
                    continue
                seen.add(marker)
                seats.append(
                    LhbSeat(
                        code=calc.suffix_code(code),
                        name=names.get(code, ""),
                        branchName=branch,
                        side=side,  # type: ignore[arg-type]
                        buyWan=buy,
                        sellWan=sell,
                        netWan=round(buy - sell, 2),
                        changePct=calc.rnd(calc.num(row.get("CHANGE_RATE"))),
                        reason=calc.text(row.get("EXPLANATION")),
                    )
                )
        ordered = sorted(stocks.values(), key=lambda s: abs(s.netWan), reverse=True)
        return DragonTigerPayload(
            tradeDate=day.isoformat(),
            stocks=ordered,
            seats=seats,
            meta=acc.meta(day.isoformat(), "close"),
        )

    async def _lhb_rows(self, acc: _Acc, kind: src.LhbKind, day: date, ttl: int) -> list[calc.Doc]:
        rows: list[calc.Doc] = []
        for page in range(1, 4):
            doc = await self._doc(
                acc,
                f"龙虎榜 {kind}",
                src.lhb_url(kind, day, page),
                src.EM_HEADERS,
                ttl=ttl,
                source="东方财富 数据中心 龙虎榜",
            )
            data = [
                calc.as_doc(r)
                for r in calc.as_list(calc.as_doc(calc.as_doc(doc).get("result")).get("data"))
            ]
            rows += data
            if len(data) < 500:
                break
        return rows

    # ───────────── 7. 连板分析 ─────────────

    async def ladder(self) -> LadderPayload:
        acc = _Acc()
        day, session, days = await self._resolve(acc)
        if day is None:
            return LadderPayload(tiers=[], brokenStreaks=[], meta=acc.meta(None, "unknown"))
        previous = date.fromisoformat(days[-2]) if len(days) >= 2 else None
        acc.notes += [
            "晋级率 = 昨日 N 板股今日继续涨停的比例；断板率 = 1 − 整体晋级率。",
            "封单金额来自东财涨停池的封板资金；涨停原因来自同花顺涨停池。",
            "断板股的「较日内最高回撤」= (现价 − 日内最高) / 昨收，仅描述当日冲高回落程度。",
        ]
        zt, yst, ths_today, ths_prev, actions = await asyncio.gather(
            self._pool(acc, "limit_up", day, "涨停池"),
            self._yesterday(acc, day),
            self._ths_pool(acc, day),
            self._ths_pool(acc, previous) if previous else _empty_ths(),
            self._jygs(acc, day),
        )
        action_rows = actions.by_code() if actions else {}
        today_rows = zt or []
        promo = (
            calc.promotion(today_rows, yst or []) if zt is not None and yst is not None else None
        )
        tiers: dict[int, list[LadderStock]] = {}
        for r in sorted(today_rows, key=lambda x: x.first_seal or "99"):
            reason = ths_today.get(r.code)
            label = (
                reason.high_days
                if reason and "天" in reason.high_days
                else calc.board_label(r.streak)
            )
            tiers.setdefault(r.streak, []).append(
                LadderStock(
                    code=calc.suffix_code(r.code),
                    name=r.name,
                    streak=r.streak,
                    boardLabel=label,
                    price=r.price,
                    changePct=r.change_pct,
                    reason=reason.reason if reason else "",
                    sector=r.industry,
                    firstSealTime=r.first_seal,
                    lastSealTime=r.last_seal,
                    breakCount=r.breaks,
                    sealAmountWan=calc.to_wan(
                        r.seal_fund
                        if r.seal_fund is not None
                        else (reason.order_amount if reason else None)
                    ),
                    turnoverPct=r.turnover,
                    floatCapYi=calc.to_yi(r.float_cap),
                    actionSector=action_rows[r.code].sector if r.code in action_rows else "",
                )
            )
        broken = await self._broken_streaks(acc, yst or [], {r.code for r in today_rows}, ths_prev)
        return LadderPayload(
            tradeDate=day.isoformat(),
            overallPromotionRatePct=None if promo is None else promo.overall_pct,
            overallBrokenRatePct=None if promo is None else promo.broken_pct,
            rate1To2Pct=None if promo is None else promo.r12,
            count1To2Text="" if promo is None else promo.t12,
            rate2To3Pct=None if promo is None else promo.r23,
            count2To3Text="" if promo is None else promo.t23,
            rate3To4PlusPct=None if promo is None else promo.r34,
            count3To4PlusText="" if promo is None else promo.t34,
            tiers=[
                LadderTier(streak=s, label=calc.board_label(s), stocks=rows)
                for s, rows in sorted(tiers.items(), reverse=True)
            ],
            brokenStreaks=broken,
            meta=acc.meta(day.isoformat(), session),
        )

    async def _broken_streaks(
        self,
        acc: _Acc,
        yesterday: list[calc.YesterdayRow],
        today_codes: set[str],
        ths_prev: Mapping[str, calc.ThsReason],
    ) -> list[BrokenStreak]:
        broken = [r for r in yesterday if r.code not in today_codes]
        if not broken:
            return []
        quotes = await self._quotes(
            acc, "断板股行情", [calc.secid_for(r.code) for r in broken], "f2,f3,f12,f13,f14,f15,f18"
        )
        out: list[BrokenStreak] = []
        for r in broken:
            quote = quotes.get(f"{calc.secid_for(r.code).split('.')[0]}.{r.code}", {})
            price, high, pre = (
                calc.num(quote.get("f2")),
                calc.num(quote.get("f15")),
                calc.num(quote.get("f18")),
            )
            drawdown = (
                None
                if price is None or high is None or not pre
                else round((price - high) / pre * 100, 2)
            )
            reason = ths_prev.get(r.code)
            out.append(
                BrokenStreak(
                    code=calc.suffix_code(r.code),
                    name=r.name,
                    yesterdayStreak=r.prev_streak,
                    price=price if price is not None else r.price,
                    changePct=r.change_pct,
                    drawdownFromHighPct=drawdown,
                    turnoverPct=r.turnover,
                    sector=r.industry,
                    reason=reason.reason if reason else "",
                )
            )
        return sorted(
            out, key=lambda b: (-b.yesterdayStreak, b.changePct if b.changePct is not None else 0.0)
        )[:60]

    # ───────────── 8. 热点消息 ─────────────

    async def news(self) -> NewsPayload:
        acc = _Acc()
        acc.notes += [
            "来源：财联社电报、东方财富 7x24、同花顺 7x24、金十快讯；"
            "同一事件按标题去重，保留最早出现的来源。",
            "影响等级与多空情绪是关键词规则判定，不是模型判断："
            "财联社 A 级 → S，B 级 → A；金十重要 → S。",
            "高信噪比 = S/A 级，或带关联个股且情绪明确；关联股票涨跌幅为当前实时值。",
        ]
        cls_doc, em_doc, ths_doc, jin_doc = await asyncio.gather(
            self._doc(
                acc,
                "财联社",
                src.cls_telegraph_url(60),
                src.CLS_HEADERS,
                ttl=20,
                source="财联社电报",
            ),
            self._doc(
                acc, "东方财富", src.em_news_url(50), src.EM_HEADERS, ttl=20, source="东方财富 7x24"
            ),
            self._doc(
                acc,
                "同花顺",
                src.ths_news_url(50),
                src.THS_NEWS_HEADERS,
                ttl=20,
                source="同花顺 7x24",
            ),
            self._doc(
                acc, "金十数据", src.jin10_url(), src.JIN10_HEADERS, ttl=20, source="金十快讯"
            ),
        )
        status = {
            name: ("ok" if doc is not None else acc.unavailable.get(name, _UNAVAILABLE_NEWS))
            for name, doc in (
                ("财联社", cls_doc),
                ("东方财富", em_doc),
                ("同花顺", ths_doc),
                ("金十数据", jin_doc),
            )
        }
        # 东财 / 同花顺只给股票代码，涨跌幅要一次批量查实时报价。
        raw_em = calc.as_list(calc.as_doc(calc.as_doc(em_doc).get("data")).get("fastNewsList"))
        raw_ths = calc.as_list(calc.as_doc(calc.as_doc(ths_doc).get("data")).get("list"))
        secids = [s for item in raw_em for s in _em_secids(calc.as_doc(item))]
        secids += [s for item in raw_ths for s in _ths_secids(calc.as_doc(item))]
        quotes = (
            await self._quotes(acc, "资讯关联行情", secids, "f2,f3,f12,f13,f14") if secids else {}
        )
        items: list[NewsItem] = []
        items += _cls_items(
            calc.as_list(calc.as_doc(calc.as_doc(cls_doc).get("data")).get("roll_data"))
        )
        items += _em_items(raw_em, quotes)
        items += _ths_items(raw_ths, quotes)
        items += _jin10_items(calc.as_list(calc.as_doc(jin_doc).get("data")))
        items.sort(key=lambda i: i.timestamp, reverse=True)
        return NewsPayload(
            items=_dedupe(items)[:150],
            sourceStatus=status,
            meta=acc.meta(
                self._local().date().isoformat(), "intraday" if self._live() else "close"
            ),
        )

    # ───────────── 8b. 韭研公社异动解析 ─────────────

    async def jygs_actions(self, requested: date | None = None) -> JygsActionsPayload:
        acc = _Acc()
        acc.notes.append(
            "来源：韭研公社人工整理的异动解析（交易日 12:00 与 15:30 前后更新），"
            "涨幅 / 价格为其采集值；板块按韭研的归类，同一股票可能出现在多个板块。"
        )
        day, session, _ = await self._resolve(acc)
        target = requested or day
        if target is None:
            return JygsActionsPayload(meta=acc.meta(None, "unknown"))
        actions = await self._jygs(acc, target)
        sectors = [
            JygsSector(
                name=s.name,
                reason=s.reason,
                stocks=[
                    JygsStock(
                        code=calc.suffix_code(r.code),
                        name=r.name,
                        boardLabel=r.board_label,
                        price=r.price,
                        changePct=r.change_pct,
                        firstSealTime=r.first_seal,
                        explain=r.explain,
                    )
                    for r in s.stocks
                ],
            )
            for s in (actions.sectors if actions else [])
        ]
        if actions is not None and not sectors:
            acc.notes.append("该日期暂无异动解析（韭研通常在交易日 12:00 后才更新当天内容）。")
        return JygsActionsPayload(
            tradeDate=target.isoformat(),
            totalStocks=actions.total if actions else 0,
            sectors=sectors,
            meta=acc.meta(target.isoformat(), session),
        )

    # ───────────── 9. 个股详情 ─────────────

    async def stock_detail(self, raw_code: str) -> StockDetailPayload:
        acc = _Acc()
        code = raw_code.split(".")[0].strip()
        if len(code) != 6 or not code.isdigit():
            raise ProviderError(
                code="INVALID_CODE", message="股票代码应为 6 位数字", status_code=422
            )
        secid = calc.secid_for(code)
        day, session, _ = await self._resolve(acc)
        quote_doc, trend_doc, kline_doc, lhb_day_doc = await asyncio.gather(
            self._doc(
                acc, "个股详情", src.stock_get_url(secid), src.EM_HEADERS, source="东方财富 行情"
            ),
            self._doc(
                acc, "个股分时", src.trends_url(secid), src.EM_HEADERS, source="东方财富 分时"
            ),
            self._doc(
                acc,
                "个股日K",
                src.daily_kline_url(secid, 90),
                src.EM_HEADERS,
                ttl=120,
                source="东方财富 日K",
            ),
            self._doc(
                acc,
                "个股龙虎榜日期",
                src.lhb_stock_latest_url(code),
                src.EM_HEADERS,
                ttl=300,
                source="东方财富 数据中心 龙虎榜",
            ),
        )
        quote = calc.as_doc(calc.as_doc(quote_doc).get("data"))
        if not quote:
            raise ProviderError(code="NOT_FOUND", message="没有找到这只股票", status_code=404)
        trend = calc.parse_trends(calc.as_doc(trend_doc)) if trend_doc else None
        intraday = (
            [
                IntradayPoint(time=r.clock, price=r.price, avgPrice=r.avg, volume=r.volume)
                for r in calc.latest_day_rows(trend)
            ]
            if trend
            else []
        )
        bars = calc.parse_klines(calc.as_doc(kline_doc)) if kline_doc else []
        pool = (
            {r.code: r for r in (await self._pool(acc, "limit_up", day, "涨停池") or [])}
            if day
            else {}
        )
        ths = await self._ths_pool(acc, day) if day else {}
        actions = await self._jygs(acc, day) if day else None
        action = actions.by_code().get(code) if actions else None
        hit, reason = pool.get(code), ths.get(code)
        float_cap = calc.num(quote.get("f117"))
        seal = hit.seal_fund if hit else None
        sectors = [calc.text(quote.get("f127"))] + [
            s for s in calc.text(quote.get("f129")).split(",") if s
        ]
        lhb_rows = calc.as_list(calc.as_doc(calc.as_doc(lhb_day_doc).get("result")).get("data"))
        lhb_day = (
            date.fromisoformat(calc.text(calc.as_doc(lhb_rows[0]).get("TRADE_DATE"))[:10])
            if lhb_rows
            else None
        )
        buys, sells, lhb_reason = await self._stock_lhb(acc, code, lhb_day)
        return StockDetailPayload(
            code=calc.suffix_code(code),
            name=calc.text(quote.get("f58")) or code,
            price=calc.num(quote.get("f43")),
            preClose=calc.num(quote.get("f60")),
            changePct=calc.num(quote.get("f170")),
            turnoverPct=calc.num(quote.get("f168")),
            floatCapYi=calc.to_yi(float_cap),
            sealAmountWan=None if seal is None else calc.to_wan(seal),
            sealFloatRatioPct=None
            if not seal or not float_cap
            else round(seal / float_cap * 100, 2),
            streakLabel=calc.board_label(hit.streak) if hit else "",
            firstSealTime=hit.first_seal if hit else "",
            sectors=[s for s in dict.fromkeys(sectors) if s][:14],
            catalystReason=reason.reason if reason else "",
            actionSector=action.sector if action else "",
            actionSectorReason=action.sector_reason if action else "",
            actionExplain=action.explain if action else "",
            intraday=intraday,
            dailyBars=[
                DailyBar(
                    date=b.day,
                    open=b.open,
                    close=b.close,
                    low=b.low,
                    high=b.high,
                    volume=b.volume,
                    changePct=b.change_pct if b.change_pct is not None else 0.0,
                )
                for b in bars
            ],
            lhbDate=lhb_day.isoformat() if lhb_day else None,
            lhbReason=lhb_reason,
            buySeats=buys,
            sellSeats=sells,
            meta=acc.meta(day.isoformat() if day else None, session),
        )

    async def _stock_lhb(
        self, acc: _Acc, code: str, day: date | None
    ) -> tuple[list[LhbSeatLine], list[LhbSeatLine], str]:
        if day is None:
            return [], [], ""
        ttl = self._ttl(day) if day < self._local().date() else 300
        buy_doc, sell_doc = await asyncio.gather(
            self._doc(
                acc, "个股龙虎榜买方", src.lhb_url("buy", day, 1, code), src.EM_HEADERS, ttl=ttl
            ),
            self._doc(
                acc, "个股龙虎榜卖方", src.lhb_url("sell", day, 1, code), src.EM_HEADERS, ttl=ttl
            ),
        )

        def lines(doc: Any | None, key: str) -> tuple[list[LhbSeatLine], str]:
            rows = [
                calc.as_doc(r)
                for r in calc.as_list(calc.as_doc(calc.as_doc(doc).get("result")).get("data"))
            ]
            rows.sort(key=lambda r: calc.num(r.get(key)) or 0.0, reverse=True)
            picked: list[LhbSeatLine] = []
            seen: set[tuple[str, float, float]] = set()
            for r in rows:
                branch = calc.text(r.get("OPERATEDEPT_NAME"))
                buy, sell = calc.to_wan(r.get("BUY")) or 0.0, calc.to_wan(r.get("SELL")) or 0.0
                if branch and (branch, buy, sell) not in seen:
                    seen.add((branch, buy, sell))
                    picked.append(
                        LhbSeatLine(
                            branchName=branch, buyWan=buy, sellWan=sell, netWan=round(buy - sell, 2)
                        )
                    )
            reason = calc.text(rows[0].get("EXPLANATION")) if rows else ""
            return picked[:5], reason

        buys, reason = lines(buy_doc, "BUY")
        sells, _ = lines(sell_doc, "SELL")
        return buys, sells, reason


# ───────────── 资讯解析（模块级，便于离线测试） ─────────────

_BRACKET = re.compile(r"^【(.+?)】")


def _title_of(title: str, body: str) -> str:
    if title.strip():
        return title.strip()
    match = _BRACKET.match(body.strip())
    return match.group(1) if match else body.strip()[:40]


def _stock_code(stock_id: str) -> str:
    return re.sub(r"^[a-zA-Z]+", "", stock_id)


def _cls_items(rows: list[Any]) -> list[NewsItem]:
    out: list[NewsItem] = []
    for raw in rows:
        row = calc.as_doc(raw)
        stamp = int(calc.num(row.get("ctime")) or 0)
        body = calc.text(row.get("content")) or calc.text(row.get("brief"))
        if not stamp or not body:
            continue
        title = _title_of(calc.text(row.get("title")), body)
        stocks = [
            NewsStock(
                code=calc.suffix_code(_stock_code(calc.text(s.get("StockID")))),
                name=calc.text(s.get("name")),
                changePct=calc.rnd(calc.num(s.get("RiseRange"))),
            )
            for s in (calc.as_doc(x) for x in calc.as_list(row.get("stock_list")))
            if _stock_code(calc.text(s.get("StockID"))).isdigit()
        ]
        hint = {"A": "S", "B": "A"}.get(calc.text(row.get("level")))
        out.append(
            _news(
                "cls-" + calc.text(row.get("id")),
                stamp,
                "财联社",
                title,
                body,
                stocks,
                [
                    calc.text(calc.as_doc(s).get("subject_name"))
                    for s in calc.as_list(row.get("subjects"))
                ],
                hint,
            )
        )
    return out


def _em_secids(row: calc.Doc) -> list[str]:
    return [
        str(s)
        for s in calc.as_list(row.get("stockList"))
        if str(s).split(".")[0] in {"0", "1", "90"}
    ]


def _em_items(rows: list[Any], quotes: Mapping[str, calc.Doc]) -> list[NewsItem]:
    out: list[NewsItem] = []
    for raw in rows:
        row = calc.as_doc(raw)
        shown = calc.text(row.get("showTime"))
        if not shown:
            continue
        stamp = int(
            datetime.strptime(shown, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI).timestamp()
        )
        stocks: list[NewsStock] = []
        sectors: list[str] = []
        for secid in _em_secids(row):
            quote = quotes.get(secid)
            if quote is None:
                continue
            if secid.startswith("90."):
                sectors.append(calc.text(quote.get("f14")))
            else:
                stocks.append(
                    NewsStock(
                        code=calc.suffix_code(calc.text(quote.get("f12"))),
                        name=calc.text(quote.get("f14")),
                        changePct=calc.rnd(calc.num(quote.get("f3"))),
                    )
                )
        body = calc.text(row.get("summary"))
        out.append(
            _news(
                "em-" + calc.text(row.get("code")),
                stamp,
                "东方财富",
                calc.text(row.get("title")),
                body,
                stocks,
                sectors,
                None,
            )
        )
    return out


def _ths_secids(row: calc.Doc) -> list[str]:
    ids: list[str] = []
    for stock in calc.as_list(row.get("stock")):
        item = calc.as_doc(stock)
        code, market = calc.text(item.get("stockCode")), calc.text(item.get("stockMarket"))
        if code.isdigit() and len(code) == 6 and market in {"17", "33"}:
            ids.append(f"{'1' if market == '17' else '0'}.{code}")
    return ids


def _ths_items(rows: list[Any], quotes: Mapping[str, calc.Doc]) -> list[NewsItem]:
    out: list[NewsItem] = []
    for raw in rows:
        row = calc.as_doc(raw)
        stamp = int(calc.num(row.get("ctime")) or 0)
        body = calc.text(row.get("digest")) or calc.text(row.get("short"))
        if not stamp or not body:
            continue
        stocks = [
            NewsStock(
                code=calc.suffix_code(secid.split(".")[1]),
                name=calc.text(quotes[secid].get("f14")),
                changePct=calc.rnd(calc.num(quotes[secid].get("f3"))),
            )
            for secid in _ths_secids(row)
            if secid in quotes
        ]
        sectors = [calc.text(calc.as_doc(f).get("name")) for f in calc.as_list(row.get("field"))]
        out.append(
            _news(
                "ths-" + calc.text(row.get("id")),
                stamp,
                "同花顺",
                calc.text(row.get("title")),
                body,
                stocks,
                sectors,
                None,
            )
        )
    return out


def _jin10_items(rows: list[Any]) -> list[NewsItem]:
    out: list[NewsItem] = []
    for raw in rows:
        row = calc.as_doc(raw)
        data = calc.as_doc(row.get("data"))
        body = calc.text(data.get("content"))
        shown = calc.text(row.get("time"))
        if not body or not shown:
            continue
        stamp = int(
            datetime.strptime(shown, "%Y-%m-%d %H:%M:%S").replace(tzinfo=SHANGHAI).timestamp()
        )
        hint = "S" if calc.num(row.get("important")) == 1 else None
        out.append(
            _news(
                "jin10-" + calc.text(row.get("id")),
                stamp,
                "金十数据",
                calc.text(data.get("title")),
                body,
                [],
                [],
                hint,
            )
        )
    return out


def _news(
    ident: str,
    stamp: int,
    source: str,
    title: str,
    body: str,
    stocks: list[NewsStock],
    sectors: list[str],
    hint: str | None,
) -> NewsItem:
    title, body = calc.strip_html(title), calc.strip_html(body)
    stocks = [s for s in stocks if calc.is_a_share_stock(s.code)]
    content = f"{title}{body}"
    impact = calc.classify_impact(content, source_hint=hint, has_stocks=bool(stocks))
    sentiment = calc.classify_sentiment(content)
    day, hhmm = calc.day_clock(stamp)
    return NewsItem(
        id=ident,
        time=hhmm,
        date=day,
        timestamp=stamp,
        source=source,  # type: ignore[arg-type]
        region="overseas"
        if calc.is_overseas(content if source == "金十数据" else title)
        else "domestic",
        impactLevel=impact,  # type: ignore[arg-type]
        sentiment=sentiment,  # type: ignore[arg-type]
        title=_title_of(title, body),
        summary=body.strip()[:280],
        isHighSnr=impact in {"S", "A"} or (bool(stocks) and sentiment != "中性"),
        sectors=calc.clean_sectors(sectors),
        stocks=stocks[:8],
    )


def _dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """同一事件多源重复：按标题归一化后去重，保留最早的一条（items 倒序，所以反着扫）。"""

    seen: dict[str, NewsItem] = {}
    for item in reversed(items):
        key = re.sub(r"[\s，。,.:：;；!！?？【】\[\]()（）]", "", item.title)[:16]
        if key and key not in seen:
            seen[key] = item
    return sorted(seen.values(), key=lambda i: i.timestamp, reverse=True)


# ───────────── 小工具 ─────────────


def _to_bytes(doc: Any) -> bytes:
    return json.dumps(doc, ensure_ascii=False).encode()


async def _all3[A, B, C](a: Awaitable[A], b: Awaitable[B], c: Awaitable[C]) -> tuple[A, B, C]:
    return await asyncio.gather(a, b, c)


async def _all4[A, B, C, D](
    a: Awaitable[A], b: Awaitable[B], c: Awaitable[C], d: Awaitable[D]
) -> tuple[A, B, C, D]:
    return await asyncio.gather(a, b, c, d)


async def _none() -> None:
    return None


async def _empty_ths() -> dict[str, calc.ThsReason]:
    return {}


_service: ASharePanoramaService | None = None


def get_panorama_service() -> ASharePanoramaService:
    global _service
    if _service is None:
        settings = get_settings()
        _service = ASharePanoramaService(
            gateway=shared_gateway(),
            jygs_session=settings.jygs_session,
            jygs_session_expires=settings.jygs_session_expires,
        )
    return _service


__all__ = ["ASharePanoramaService", "get_panorama_service"]
