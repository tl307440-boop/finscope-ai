"""`factor_rank` 工具：在一组证券上按日线因子做横截面排名。

范围只有两种：本回合 instrument_search 搜到过的证券，或本库已入库日线够长的证券。
后者不是全市场，结论只对这个范围成立，source 里写明。计算在 `app.services.factor_rank`。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.repositories.instruments import InstrumentRecord, InstrumentRepository
from app.repositories.market_bars import MarketBarRepository
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.assistant.tools.market import BAR_STALE_DAYS
from app.services.factor_rank import FACTORS, FactorName, factor_value, rank_values

MAX_UNIVERSE = 30
MAX_ROWS = 15
LOOKBACK_FACTOR = 3  # 取 window × 3 个自然日，保证够 window 个交易日
SOURCE = "本系统日线因子排名（范围仅限本库已入库的证券，不是全市场）"
_LABELS: dict[str, str] = {
    "momentum": "区间涨跌幅",
    "volatility": "年化波动率",
    "drawdown": "区间最大回撤",
}


class FactorRankArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    factor: Literal["momentum", "volatility", "drawdown"] = Field(
        description="momentum 区间涨跌幅；volatility 年化波动率；drawdown 区间最大回撤"
    )
    window: int = Field(default=60, ge=5, le=250, description="回看交易日数")
    universe: Literal["searched", "stored"] = Field(
        default="stored",
        description="searched 只排本轮 instrument_search 搜到的证券；stored 排本库日线够长的证券",
    )
    descending: bool = Field(default=True, description="true 数值大的排前面")
    as_of: date | None = Field(default=None, description="按这个日期当时已有的日线算，默认今天")


class FactorRankTool:
    name: ClassVar[str] = "factor_rank"
    description: ClassVar[str] = (
        "按日线因子（区间涨跌幅、年化波动率、最大回撤）给一组证券排名，最多返回 15 行。"
        "stored 范围只是本库已收录的证券，不是全市场，回答时必须说明范围。"
        "排名只描述历史特征，不是收益预测或推荐。"
    )
    args_model: ClassVar[type[BaseModel]] = FactorRankArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, FactorRankArgs)
        factor: FactorName = args.factor
        assert factor in FACTORS
        title = f"{_LABELS[factor]}排名（近 {args.window} 个交易日）"
        pit = effective_as_of(args.as_of, ctx.today_cn)
        repository = MarketBarRepository(ctx.engine)
        provider_id = repository.provider_id_for_market("CN")
        instruments = InstrumentRepository(ctx.engine)
        if args.universe == "searched":
            ids: list[UUID] = sorted(ctx.searched, key=str)[:MAX_UNIVERSE]
        else:
            ids = repository.instruments_with_daily(
                provider_id=provider_id,
                adjustment="none",
                min_bars=args.window + 1,
                pit=pit,
                limit=MAX_UNIVERSE,
            )
        if len(ids) < 2:
            return unavailable(self.name, title, pit, "可排名的证券不足 2 只", SOURCE)

        start = pit - timedelta(days=args.window * LOOKBACK_FACTOR)
        values: dict[str, Decimal] = {}
        meta: dict[str, tuple[InstrumentRecord, date]] = {}
        stale = short = 0
        for instrument_id in ids:
            record = instruments.get(instrument_id)
            if record is None or record.market != "CN":
                continue
            rows, _ = repository.list_daily(
                instrument_id=instrument_id,
                provider_id=provider_id,
                adjustment="none",
                start=start,
                end=pit,
                pit=pit,
                limit=args.window + 1,
            )
            if rows and (pit - rows[-1].trade_date).days > BAR_STALE_DAYS:
                stale += 1
                continue
            value = factor_value([r.close for r in rows], factor, args.window)
            if value is None:
                short += 1
                continue
            key = str(instrument_id)
            values[key] = value
            meta[key] = (record, rows[-1].trade_date)
        if len(values) < 2:
            return unavailable(
                self.name,
                title,
                pit,
                f"日线够用的证券不足 2 只（过期 {stale} 只，样本不足 {short} 只）",
                SOURCE,
            )
        ranked = rank_values(values, args.descending)
        shown = ranked[:MAX_ROWS]
        notes = [f"参与排名 {len(values)} 只"]
        if stale or short:
            notes.append(f"剔除：日线过期 {stale} 只、样本不足 {short} 只")
        notes.append("用未复权收盘价计算，除权除息可能影响涨跌幅与回撤")
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("rank", "名次", "", "number"),
                Column("symbol", "代码", "", "text"),
                Column("name", "名称", "", "text"),
                Column("value", _LABELS[factor], "%", "number"),
                Column("last_date", "最新日线", "", "date"),
            ),
            rows=tuple(
                {
                    "rank": str(rank),
                    "symbol": meta[key][0].display_symbol,
                    "name": meta[key][0].name_cn or meta[key][0].name_en,
                    "value": format(value, "f"),
                    "last_date": meta[key][1].isoformat(),
                }
                for rank, key, value in shown
            ),
            as_of=pit,
            source=SOURCE,
            reason="；".join(notes),
            truncated=max(0, len(ranked) - len(shown)),
        )
