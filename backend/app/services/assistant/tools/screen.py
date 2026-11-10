"""`strategy_screen` 工具：均线参数稳健筛选（折外稳健分 + 交易数门槛 + 样本外检验）。

只读已入库日线，不写任何东西，所以不需要确认卡；计算在 `app.services.strategy_screen`。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.backtest import (
    DEFAULT_BACKTEST_DAYS,
    MAX_BACKTEST_BARS,
    UNADJUSTED_NOTE,
    load_bars,
)
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.market import _market_today, _resolve
from app.services.strategy_screen import MAX_COMBOS, screen_sma

TOP_N = 5
SOURCE = (
    "本系统日线参数筛选（样本内分折回测，稳健分=0.6×折收益中位数+0.4×最差折；"
    "最优组合再跑样本外尾段）"
)


class StrategyScreenArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument_id: str = Field(
        description="instrument_search 返回的证券 id", min_length=1, max_length=64
    )
    fast_values: list[int] = Field(
        default=[3, 5, 10], min_length=1, max_length=6, description="候选快线天数"
    )
    slow_values: list[int] = Field(
        default=[10, 20, 30], min_length=1, max_length=6, description="候选慢线天数"
    )
    folds: int = Field(default=4, ge=2, le=6, description="样本内分几折")
    min_trades: int = Field(default=6, ge=1, le=100, description="各折交易数合计的最低门槛")
    fee_bps: int = Field(default=10, ge=0, le=200, description="单边费率，基点")
    as_of: date | None = Field(default=None, description="按这个日期当时已有的日线算，默认今天")

    @model_validator(mode="after")
    def _check(self) -> "StrategyScreenArgs":
        if any(v < 1 or v > 250 for v in self.fast_values + self.slow_values):
            raise ValueError("均线天数必须在 1 到 250 之间")
        pairs = {(f, s) for f in self.fast_values for s in self.slow_values if f < s}
        if not pairs:
            raise ValueError("没有 fast < slow 的组合")
        if len(pairs) > MAX_COMBOS:
            raise ValueError(f"参数组合最多 {MAX_COMBOS} 组")
        return self


class StrategyScreenTool:
    name: ClassVar[str] = "strategy_screen"
    description: ClassVar[str] = (
        "对一只证券的均线参数做稳健筛选：样本内分折回测，按折外稳健分排名，"
        "交易数不够的组合淘汰，第一名再用没参与筛选的尾段做样本外检验并对比买入持有。"
        "第一行的样本外列才是检验结果；必须同时说明测试了多少组参数。"
        "这是历史描述，参数是事后挑的，不是收益预测或买卖建议。"
    )
    args_model: ClassVar[type[BaseModel]] = StrategyScreenArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, StrategyScreenArgs)
        found = _resolve(ctx, args.instrument_id)
        if isinstance(found, str):
            return unavailable(self.name, "参数筛选", ctx.today_cn, found)
        title = f"{found.name_cn or found.name_en}（{found.display_symbol}）均线参数筛选"
        pit = effective_as_of(args.as_of, _market_today(ctx, found))
        start = pit - timedelta(days=DEFAULT_BACKTEST_DAYS)
        bars, truncated, adjustment = load_bars(
            ctx, found.id, found.market, start, pit, pit, MAX_BACKTEST_BARS
        )
        try:
            result = screen_sma(
                bars,
                args.fast_values,
                args.slow_values,
                folds=args.folds,
                min_trades=args.min_trades,
                fee_bps=Decimal(args.fee_bps),
            )
        except ValueError as exc:
            return unavailable(self.name, title, pit, str(exc), SOURCE)
        if not result.ranked:
            return unavailable(
                self.name,
                title,
                pit,
                f"测试了 {result.tested} 组参数，没有一组满足交易数门槛"
                f"（淘汰 {result.rejected_trades} 组，折太短跑不了 {result.skipped_short} 组）",
                SOURCE,
            )
        top = result.ranked[:TOP_N]
        rows = []
        for index, item in enumerate(top):
            first = index == 0
            rows.append(
                {
                    "rank": str(index + 1),
                    "fast": str(item.fast),
                    "slow": str(item.slow),
                    "robust": format(item.robust_pct, "f"),
                    "median": format(item.median_pct, "f"),
                    "worst": format(item.worst_pct, "f"),
                    "trades": str(item.trades),
                    "oos": (
                        format(result.oos_return_pct, "f")
                        if first and result.oos_return_pct is not None
                        else None
                    ),
                    "oos_hold": (
                        format(result.oos_buy_hold_pct, "f")
                        if first and result.oos_buy_hold_pct is not None
                        else None
                    ),
                }
            )
        notes = [
            f"共测试 {result.tested} 组参数，通过交易数门槛 {len(result.ranked)} 组"
            f"（淘汰 {result.rejected_trades} 组，折太短 {result.skipped_short} 组）",
            f"样本内 {result.in_sample_bars} 根分 {result.folds} 折，样本外 {result.oos_bars} 根",
        ]
        if result.oos_return_pct is None:
            notes.append("样本外尾段太短，没能做检验")
        if adjustment != "qfq":
            notes.append(UNADJUSTED_NOTE)
        if truncated:
            notes.append("日线超过上限，只用了最近一段")
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("rank", "名次", "", "number"),
                Column("fast", "快线", "天", "number"),
                Column("slow", "慢线", "天", "number"),
                Column("robust", "稳健分", "%", "number"),
                Column("median", "折收益中位数", "%", "number"),
                Column("worst", "最差折", "%", "number"),
                Column("trades", "交易数", "次", "number"),
                Column("oos", "样本外收益", "%", "number"),
                Column("oos_hold", "样本外买入持有", "%", "number"),
            ),
            rows=tuple(rows),
            as_of=pit,
            source=SOURCE,
            reason="；".join(notes),
            truncated=1 if truncated else 0,
        )
