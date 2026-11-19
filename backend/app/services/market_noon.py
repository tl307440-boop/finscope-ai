"""北证市场「11:30 成交额」快照采集。

为什么需要单独采集：腾讯的报价接口给的是**当日累计成交额**，日 K 给的是当天收盘的总额，
5 日分时对北证 50 只返回「时刻 / 价格 / 累计量（手）」三列、没有成交额。也就是说，
北证没有任何接口能取到「11:30 那一刻的成交额」。但这个累计值在 11:30 收盘后、13:00 开盘前
恒等于上午收盘时的成交额，所以交易日在这个窗口里抓一次落库即可。

全市场午盘 = 沪 + 深 + 京，缺北证这一份整列就是空的（见 ``ashare_panorama._volume_series``）。
快照只能从首次上线当天开始累积，历史无法回补。
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from sqlalchemy import Engine

from app.providers import tencent_quote as tq
from app.providers.errors import ProviderError
from app.repositories.instruments import InstrumentRepository
from app.repositories.market_noon import MarketNoonRepository
from app.services.market_cache import PacedGateway, shared_gateway
from app.utils.market_time import SHANGHAI

logger = logging.getLogger(__name__)

# 北证市场用北证50（0.899050）代表；与 ashare_panorama._VOLUME_INDEXES 里的 bj 口径一致。
NOON_MARKET = "bj"
NOON_SECID = "0.899050"
NOON_SOURCE = "腾讯财经 报价（11:30 累计成交额）"
# 回看窗口：接口只按最近的交易日取快照，40 天足够覆盖长假。
SNAPSHOT_LOOKBACK_DAYS = 40


class NoonWriter(Protocol):
    """写入边界；测试注入假实现，不必连数据库。"""

    def upsert(
        self,
        *,
        trade_date: date,
        market: str,
        amount_yuan: Decimal,
        captured_at: datetime,
        source: str,
    ) -> None: ...


def parse_noon_amount(payload: bytes, code: str) -> Decimal | None:
    """从腾讯报价里取当日累计成交额（元）；没有这个字段就返回 None。"""

    quote = tq.parse_quotes(payload).get(code)
    if quote is None or quote.amount_yuan is None:
        return None
    return Decimal(str(quote.amount_yuan)).quantize(Decimal("0.01"))


async def _session_open(engine: Engine, gateway: PacedGateway, code: str, trade_date: date) -> bool:
    """判断 ``trade_date`` 是不是交易日。

    优先用库里的交易日历（BSE）；日历里没有这一天时，退一步看腾讯日 K 的最后一根是不是
    这一天——两边都给不出结论就按「不可用」处理，绝不猜，避免把上一个交易日的累计值
    写到节假日名下。
    """

    opened = InstrumentRepository(engine).calendar_open("BSE", trade_date)
    if opened is not None:
        return opened
    try:
        payload = await gateway.get(tq.kline_url(code, 2), tq.HEADERS)
        bars = tq.parse_daily(payload, code)
    except ProviderError:
        return False
    return bool(bars) and bars[-1].day == trade_date.isoformat()


async def capture_noon_amounts(
    engine: Engine,
    *,
    now: datetime,
    gateway: PacedGateway | None = None,
    writer: NoonWriter | None = None,
    session_open: Callable[[], Awaitable[bool]] | None = None,
) -> int:
    """抓一次北证市场 11:30 累计成交额落库；返回写入行数（非交易日/无成交额为 0）。"""

    code = tq.tencent_code(NOON_SECID)
    if code is None:  # pragma: no cover - secid 是常量，取不到码属于配置写错
        raise ProviderError(
            code="VALIDATION_ERROR", message="北证口径的代码映射缺失", status_code=500
        )
    local = now.astimezone(SHANGHAI)
    trade_date = local.date()
    own = gateway if gateway is not None else shared_gateway()
    checker = session_open or (lambda: _session_open(engine, own, code, trade_date))
    if not await checker():
        logger.info("market noon snapshot skipped: %s 不是交易日", trade_date.isoformat())
        return 0
    payload = await own.get(tq.quote_url([code]), tq.HEADERS)
    amount = parse_noon_amount(payload, code)
    if amount is None:
        raise ProviderError(
            code="UPSTREAM_ERROR", message="腾讯报价没有返回北证市场成交额", status_code=503
        )
    target = writer if writer is not None else MarketNoonRepository(engine)
    target.upsert(
        trade_date=trade_date,
        market=NOON_MARKET,
        amount_yuan=amount,
        captured_at=now,
        source=NOON_SOURCE,
    )
    logger.info(
        "market noon snapshot stored market=%s trade_date=%s amount_yuan=%s",
        NOON_MARKET,
        trade_date.isoformat(),
        amount,
    )
    return 1


def noon_snapshot(engine: Engine, *, reference: datetime | None = None) -> dict[str, float]:
    """区间内的北证午盘快照，形状是 ``{ISO 日期: 成交额(元)}``。"""

    today = (reference or datetime.now(SHANGHAI)).astimezone(SHANGHAI).date()
    return MarketNoonRepository(engine).amount_yuan_by_day(
        NOON_MARKET,
        start=today - timedelta(days=SNAPSHOT_LOOKBACK_DAYS),
        end=today,
    )
