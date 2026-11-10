"""`run_backtest` 工具：对一只证券的日线跑均线交叉回测（规格 §7）。

只读本系统已入库的日线，只认本回合 `instrument_search` 返回过的证券 id。
计算在 `app.services.backtest`（纯函数）里，这里只做取数、校验和成表。
结果是历史描述，不是收益预测；成交口径写在表的 source 里。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.repositories.market_bars import MarketBarRepository
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.market import (
    MAX_BAR_SPAN_DAYS,
    _market_today,
    _resolve,
)
from app.services.backtest import Bar, run_sma_cross

MAX_BACKTEST_BARS = 750
DEFAULT_BACKTEST_DAYS = 3 * 365
SOURCE = "本系统日线回测（信号用当日收盘价，下一交易日开盘价成交，只做多，含单边费率）"


UNADJUSTED_NOTE = "库里没有前复权日线，改用未复权价，除权除息日可能出现假跳空，收益仅供参考"


def load_bars(
    ctx: ToolContext,
    instrument_id: UUID,
    market: str,
    start: date,
    end: date,
    pit: date,
    limit: int,
) -> tuple[list[Bar], bool, str]:
    """取日线转成回测用的 Bar：优先前复权，没有就退回未复权，并返回实际用的口径。"""

    repository = MarketBarRepository(ctx.engine)
    provider_id = repository.provider_id_for_market(market)
    for adjustment in ("qfq", "none"):
        rows, truncated = repository.list_daily(
            instrument_id=instrument_id,
            provider_id=provider_id,
            adjustment=adjustment,
            start=start,
            end=end,
            pit=pit,
            limit=limit,
        )
        if rows:
            return [Bar(r.trade_date, r.open, r.close) for r in rows], truncated, adjustment
    return [], False, "qfq"


class RunBacktestArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(
        description="instrument_search 返回的证券 id", min_length=1, max_length=64
    )
    fast: int = Field(default=5, ge=1, le=120, description="快线均线天数")
    slow: int = Field(default=20, ge=2, le=250, description="慢线均线天数，必须大于快线")
    fee_bps: int = Field(default=10, ge=0, le=200, description="单边费率，基点（10 = 0.1%）")
    start: date | None = Field(default=None, description="起点 YYYY-MM-DD，默认约 3 年前")
    end: date | None = Field(default=None, description="终点 YYYY-MM-DD，默认今天")
    as_of: date | None = Field(default=None, description="按这个日期当时已有的日线算，默认今天")

    @model_validator(mode="after")
    def _check(self) -> "RunBacktestArgs":
        if self.fast >= self.slow:
            raise ValueError("fast 必须小于 slow")
        if self.start and self.end:
            if self.start > self.end:
                raise ValueError("start 不能晚于 end")
            if (self.end - self.start).days > MAX_BAR_SPAN_DAYS:
                raise ValueError("start 与 end 的跨度不能超过 3 年")
        return self


class RunBacktestTool:
    name: ClassVar[str] = "run_backtest"
    description: ClassVar[str] = (
        "用已入库的前复权日线对一只证券回测均线交叉策略（快线上穿慢线持有、下穿空仓，只做多）。"
        "返回总收益、年化、最大回撤、同期买入持有收益、交易次数、胜率、持仓占比。"
        "交易次数少于 3 次时统计意义很弱，必须如实说明；结果是历史描述，不是收益预测或买卖建议。"
    )
    args_model: ClassVar[type[BaseModel]] = RunBacktestArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, RunBacktestArgs)
        found = _resolve(ctx, args.instrument_id)
        if isinstance(found, str):
            return unavailable(self.name, "回测", ctx.today_cn, found)
        name = f"{found.name_cn or found.name_en}（{found.display_symbol}）"
        title = f"{name}均线 {args.fast}/{args.slow} 回测"
        pit = effective_as_of(args.as_of, _market_today(ctx, found))
        end = min(args.end, pit) if args.end else pit
        start = args.start or end - timedelta(days=DEFAULT_BACKTEST_DAYS)
        bars, truncated, adjustment = load_bars(
            ctx, found.id, found.market, start, end, pit, MAX_BACKTEST_BARS
        )
        if len(bars) < args.slow + 2:
            return unavailable(
                self.name,
                title,
                pit,
                f"区间内只有 {len(bars)} 根已入库日线，不够 slow={args.slow} 的回测",
                SOURCE,
            )
        try:
            result = run_sma_cross(bars, args.fast, args.slow, Decimal(args.fee_bps))
        except ValueError as exc:
            return unavailable(self.name, title, pit, str(exc), SOURCE)
        notes = []
        if adjustment != "qfq":
            notes.append(UNADJUSTED_NOTE)
        if not result.enough_trades:
            notes.append(f"只成交 {result.trades} 次，统计意义很弱")
        if truncated:
            notes.append("日线超过上限，只用了最近一段")
        metrics = (
            ("总收益", result.total_return_pct, "%"),
            ("年化收益", result.annualized_pct, "%"),
            ("最大回撤", result.max_drawdown_pct, "%"),
            ("同期买入持有收益", result.buy_hold_return_pct, "%"),
            ("交易次数", Decimal(result.trades), "次"),
            ("胜率", result.win_rate_pct, "%"),
            ("持仓占比", result.exposure_pct, "%"),
            ("样本日线数", Decimal(result.bars), "根"),
        )
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("metric", "指标", "", "text"),
                Column("value", "数值", "", "number"),
                Column("unit", "单位", "", "text"),
            ),
            rows=tuple(
                {
                    "metric": label,
                    "value": None if value is None else format(value, "f"),
                    "unit": unit,
                }
                for label, value, unit in metrics
            ),
            as_of=pit,
            source=SOURCE,
            status="ok",
            reason="；".join(notes) or None,
            truncated=1 if truncated else 0,
        )
