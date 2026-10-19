"""自定义指标与自选：`custom_indicator`、`watchlist`（规格 §7）。

`custom_indicator` 只答「当前」：这些指标的分位按全历史序列计算，不接受历史时点，
拿它回答历史时点会有前视（R8），所以 `as_of` 早于今天直接返回没有数据并说明原因。
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.repositories.custom_analysis import CustomAnalysisRepository
from app.repositories.macro import decimal_text
from app.repositories.watchlists import WatchlistRepository
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext
from app.services.custom_analysis import CustomAnalysisService
from app.services.custom_analysis_rules import GROUPS_BY_CODE, OPTION_GROUPS

PCR_DAYS = 60
MAX_WATCH_ROWS = 100
_HISTORY_REASON = "这个指标的分位按全历史计算，不支持历史时点，只能回答当前"
_SOURCE = "本系统自定义分析（交易所期权成交 / 指数估值数据）"


def _num(value: float | int | None) -> str | None:
    """这些指标在服务里是浮点；这里统一保留 4 位小数再转字符串，不带多余的 0。"""

    if value is None:
        return None
    return decimal_text(Decimal(str(round(float(value), 4))))


class CustomIndicatorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: Literal["pcr", "valuation"] = Field(
        description="pcr=期权认沽认购比（需要 group）；valuation=股债收益差、巴菲特指标等估值指标"
    )
    group: str | None = Field(
        default=None,
        description="pcr 的分组代码：" + "、".join(f"{g.code}={g.name}" for g in OPTION_GROUPS),
    )
    as_of: date | None = Field(default=None, description="只能是今天或不填；更早的时点不支持")

    @model_validator(mode="after")
    def _check(self) -> "CustomIndicatorArgs":
        if self.indicator == "pcr":
            if self.group is None:
                raise ValueError("pcr 需要 group")
            if self.group not in GROUPS_BY_CODE:
                raise ValueError(f"没有这个分组：{self.group}")
        return self


class CustomIndicatorTool:
    name: ClassVar[str] = "custom_indicator"
    description: ClassVar[str] = (
        "本系统自定义指标，只答当前。pcr：某个指数期权的认沽认购比"
        "（成交量与持仓量 PCR、5 年分位、情绪信号，最近 60 个交易日）；"
        "valuation：股债收益差、巴菲特指标、中位数市净率的当前值、分位与区间。"
    )
    args_model: ClassVar[type[BaseModel]] = CustomIndicatorArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, CustomIndicatorArgs)
        today = ctx.today_cn
        title = "期权认沽认购比" if args.indicator == "pcr" else "估值类自定义指标"
        if args.as_of is not None and args.as_of < today:
            return unavailable(self.name, title, args.as_of, _HISTORY_REASON, _SOURCE)
        service = CustomAnalysisService(CustomAnalysisRepository(ctx.engine), today=lambda: today)
        if args.indicator == "pcr":
            return self._pcr(service, args, today)
        return self._valuation(service, today)

    def _pcr(
        self, service: CustomAnalysisService, args: CustomIndicatorArgs, today: date
    ) -> ToolResult:
        view = service.pcr(args.group or "")
        if view is None:
            return unavailable(self.name, "期权认沽认购比", today, "没有这个分组", _SOURCE)
        title = f"{view.group_name}期权认沽认购比"
        rows: list[dict[str, str | None]] = []
        reasons: list[str] = []
        stale = False
        for basket in view.baskets:
            if basket.status != "ok" and basket.reason:
                reasons.append(f"{basket.label}：{basket.reason}")
            stale = stale or basket.status == "stale"
            for point in basket.points[-PCR_DAYS:]:
                rows.append(
                    {
                        "basket": basket.label,
                        "trade_date": point.trade_date.isoformat(),
                        "volume_pcr": _num(point.volume_pcr),
                        "oi_pcr": _num(point.oi_pcr),
                        "percentile": _num(point.volume_pcr_percentile_5y),
                        "signal": point.sentiment_signal,
                    }
                )
        if not rows:
            return unavailable(
                self.name, title, today, "；".join(reasons) or "还没有入库的期权数据", _SOURCE
            )
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("basket", "合约组", "", "text"),
                Column("trade_date", "交易日", "", "date"),
                Column("volume_pcr", "成交量 PCR", "", "number"),
                Column("oi_pcr", "持仓量 PCR", "", "number"),
                Column("percentile", "成交量 PCR 5 年分位", "%", "number"),
                Column("signal", "情绪信号", "", "text"),
            ),
            rows=tuple(rows),
            as_of=today,
            source=_SOURCE,
            status="stale" if stale else "ok",
            reason="；".join(reasons) or None,
        )

    def _valuation(self, service: CustomAnalysisService, today: date) -> ToolResult:
        view = service.valuation()
        rows: list[dict[str, str | None]] = []
        reasons: list[str] = []
        stale = False
        for indicator in view.indicators:
            if indicator.status != "ok" and indicator.reason:
                reasons.append(f"{indicator.name}：{indicator.reason}")
            stale = stale or indicator.status == "stale"
            latest = indicator.latest
            if latest is None:
                continue
            rows.append(
                {
                    "name": indicator.name,
                    "trade_date": latest.trade_date.isoformat(),
                    "value": _num(latest.value),
                    "unit": indicator.unit,
                    "percentile": _num(latest.percentile_5y),
                    "zone": indicator.zone,
                }
            )
        if not rows:
            return unavailable(
                self.name,
                "估值类自定义指标",
                today,
                "；".join(reasons) or "还没有入库数据",
                _SOURCE,
            )
        return ToolResult(
            tool=self.name,
            title="估值类自定义指标",
            columns=(
                Column("name", "指标", "", "text"),
                Column("trade_date", "日期", "", "date"),
                Column("value", "当前值", "", "number"),
                Column("unit", "单位", "", "text"),
                Column("percentile", "5 年分位", "%", "number"),
                Column("zone", "区间", "", "text"),
            ),
            rows=tuple(rows),
            as_of=today,
            source=_SOURCE,
            status="stale" if stale else "ok",
            reason="；".join(reasons) or None,
        )


class WatchlistArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WatchlistTool:
    name: ClassVar[str] = "watchlist"
    description: ClassVar[str] = (
        "读当前用户自选里的标的和最近一个已入库的收盘价。只读，最多 100 条。"
    )
    args_model: ClassVar[type[BaseModel]] = WatchlistArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        today = ctx.today_cn
        repository = WatchlistRepository(ctx.engine)
        groups = repository.list_groups(ctx.user_id)
        ids = [item.instrument_id for group in groups for item in group.items if item.instrument_id]
        closes = repository.recent_closes(ids, today - timedelta(days=14))
        rows: list[dict[str, str | None]] = []
        for group in groups:
            for item in group.items:
                recent = closes.get(item.instrument_id, []) if item.instrument_id else []
                last = recent[-1] if recent else None
                rows.append(
                    {
                        "group": group.name,
                        "symbol": item.display_symbol,
                        "name": item.name_cn,
                        "close": None if last is None else decimal_text(last.close),
                        "trade_date": None if last is None else last.trade_date.isoformat(),
                    }
                )
        if not rows:
            return unavailable(self.name, "我的自选", today, "自选里还没有标的", "本系统自选")
        shown = rows[:MAX_WATCH_ROWS]
        return ToolResult(
            tool=self.name,
            title="我的自选",
            columns=(
                Column("group", "分组", "", "text"),
                Column("symbol", "代码", "", "text"),
                Column("name", "名称", "", "text"),
                Column("close", "最近收盘", "", "number"),
                Column("trade_date", "收盘日", "", "date"),
            ),
            rows=tuple(shown),
            as_of=today,
            source="本系统自选与已入库日线",
            truncated=len(rows) - len(shown),
        )
