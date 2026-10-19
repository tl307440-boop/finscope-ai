"""宏观序列工具：`macro_series`、`macro_compare`（规格 §7）。

美国与中国序列写在同一组表，统一走 `MacroRepository.list_vintages` + `select_visible`；
时点按序列所属市场换算（美国纽约日历日、中国上海日历日）。发布日当天不算已公布（`vintage_visible`）。
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.catalog.macro_series import fred_series_catalog
from app.providers.vintage import VintagePoint, select_visible
from app.repositories.macro import MacroRepository, decimal_text
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tools.base import ToolContext, effective_as_of
from app.services.china_macro import china_series_catalog, is_stale

MAX_ROWS = 120
MAX_SPAN_DAYS = 3653  # 10 年
DEFAULT_SPAN_DAYS = 3 * 366


@dataclass(frozen=True)
class SeriesMeta:
    code: str
    name: str
    unit: str
    frequency: str
    source: str
    china: bool


def _unit(base_unit: str) -> str:
    return "%" if base_unit == "Percent" else base_unit


def _catalog() -> dict[str, SeriesMeta]:
    found: dict[str, SeriesMeta] = {}
    for seed in fred_series_catalog():
        source = "FRED" if seed.provider == "fred" else "东方财富数据中心（美国经济数据）"
        found[seed.series_code] = SeriesMeta(
            seed.series_code,
            seed.name_cn,
            _unit(seed.base_unit),
            seed.frequency,
            source,
            False,
        )
    for spec in china_series_catalog():
        found[spec.series_code] = SeriesMeta(
            spec.series_code,
            spec.name_cn,
            _unit(spec.base_unit),
            spec.frequency,
            f"{spec.source_agency}（本系统入库）",
            True,
        )
    return found


SERIES = _catalog()
_CODE_HELP = "序列代码，如 CPIAUCSL（美国 CPI）、UNRATE、DGS10、CPI_CN（中国 CPI）、PMI_CN、M2_CN"


def catalog_help() -> str:
    """给模型的序列清单（代码 + 中文名），写进工具说明。排序固定，保证提示词前缀不变。"""

    return "；".join(f"{code}={meta.name}" for code, meta in sorted(SERIES.items()))


class _Window(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: date | None = Field(default=None, description="观察日起点 YYYY-MM-DD，默认约 3 年前")
    end: date | None = Field(default=None, description="观察日终点 YYYY-MM-DD，默认今天")
    as_of: date | None = Field(default=None, description="按这个日期当时已公布的数据查，默认今天")

    @model_validator(mode="after")
    def _check_window(self) -> "_Window":
        if self.start and self.end:
            if self.start > self.end:
                raise ValueError("start 不能晚于 end")
            if (self.end - self.start).days > MAX_SPAN_DAYS:
                raise ValueError("start 与 end 的跨度不能超过 10 年")
        return self


class MacroSeriesArgs(_Window):
    code: str = Field(description=_CODE_HELP, min_length=1, max_length=32)

    @field_validator("code")
    @classmethod
    def _known(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in SERIES:
            raise ValueError(f"没有这条序列：{value}")
        return code


class MacroCompareArgs(_Window):
    codes: list[str] = Field(description="要对比的两条序列代码", min_length=2, max_length=2)

    @field_validator("codes")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip().upper() for item in value]
        for original, code in zip(value, cleaned, strict=True):
            if code not in SERIES:
                raise ValueError(f"没有这条序列：{original}")
        if cleaned[0] == cleaned[1]:
            raise ValueError("两条序列不能相同")
        return cleaned


@dataclass(frozen=True)
class _Loaded:
    meta: SeriesMeta
    points: list[VintagePoint]
    pit: date
    status: str  # ok | stale
    reason: str | None


def _load(ctx: ToolContext, meta: SeriesMeta, args: _Window) -> _Loaded | str:
    """读出某条序列在时点 pit 当时已公布的观测；读不出来返回原因文字。"""

    today = ctx.today_cn if meta.china else ctx.today_us
    pit = effective_as_of(args.as_of, today)
    end = min(args.end, pit) if args.end else pit
    start = args.start or end - timedelta(days=DEFAULT_SPAN_DAYS)
    raw = MacroRepository(ctx.engine).list_vintages(meta.code, start=start, end=end)
    if raw is None or not raw:
        return f"{meta.name}还没有入库观测"
    visible = select_visible(raw, pit)
    if not visible:
        return f"{meta.name}在 {pit.isoformat()} 之前没有已公布的观测"
    latest = visible[-1].observation_date
    if is_stale(latest, pit, meta.frequency):
        status, reason = (
            "stale",
            f"最新观测日是 {latest.isoformat()}，距 {pit.isoformat()} 已超过正常更新间隔，"
            "可能尚未更新",
        )
    else:
        status, reason = "ok", None
    return _Loaded(meta, visible, pit, status, reason)


class MacroSeriesTool:
    name: ClassVar[str] = "macro_series"
    description: ClassVar[str] = (
        "查一条宏观序列在某个时点当时已公布的观测（美国 FRED 序列与中国宏观序列），"
        "返回观察日、数值、发布日，最多最近 120 条。可用序列：" + catalog_help()
    )
    args_model: ClassVar[type[BaseModel]] = MacroSeriesArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, MacroSeriesArgs)
        meta = SERIES[args.code]
        today = ctx.today_cn if meta.china else ctx.today_us
        loaded = _load(ctx, meta, args)
        if isinstance(loaded, str):
            return unavailable(
                self.name, meta.name, effective_as_of(args.as_of, today), loaded, meta.source
            )
        shown = loaded.points[-MAX_ROWS:]
        return ToolResult(
            tool=self.name,
            title=meta.name,
            columns=(
                Column("observation_date", "观察日", "", "date"),
                Column("value", "数值", meta.unit, "number"),
                Column("published", "发布日", "", "date"),
            ),
            rows=tuple(
                {
                    "observation_date": point.observation_date.isoformat(),
                    "value": decimal_text(point.value),
                    "published": point.realtime_start.isoformat(),
                }
                for point in shown
            ),
            as_of=loaded.pit,
            source=meta.source,
            status="stale" if loaded.status == "stale" else "ok",
            reason=loaded.reason,
            truncated=len(loaded.points) - len(shown),
            primary="value",
        )


class MacroCompareTool:
    name: ClassVar[str] = "macro_compare"
    description: ClassVar[str] = (
        "把两条频率相同的宏观序列按观察日对齐后并排给出（最多最近 120 个共同观察日）。"
        "频率不同（如月度对季度）会返回没有数据。可用序列同 macro_series。"
    )
    args_model: ClassVar[type[BaseModel]] = MacroCompareArgs

    def run(self, ctx: ToolContext, args: Any) -> ToolResult:
        assert isinstance(args, MacroCompareArgs)
        first, second = (SERIES[code] for code in args.codes)
        title = f"{first.name} 对比 {second.name}"
        today = ctx.today_cn if first.china else ctx.today_us
        fallback = effective_as_of(args.as_of, today)
        if first.frequency != second.frequency:
            return unavailable(
                self.name, title, fallback, "两条序列的频率不同，按观察日对齐没有意义", first.source
            )
        loaded = [_load(ctx, meta, args) for meta in (first, second)]
        for item in loaded:
            if isinstance(item, str):
                return unavailable(self.name, title, fallback, item, first.source)
        left, right = loaded
        assert not isinstance(left, str) and not isinstance(right, str)
        by_date = {point.observation_date: point for point in right.points}
        pairs = [
            (point, by_date[point.observation_date])
            for point in left.points
            if point.observation_date in by_date
        ]
        if not pairs:
            return unavailable(self.name, title, fallback, "两条序列没有共同的观察日", first.source)
        shown = pairs[-MAX_ROWS:]
        stale = [item.reason for item in (left, right) if item.reason]
        return ToolResult(
            tool=self.name,
            title=title,
            columns=(
                Column("observation_date", "观察日", "", "date"),
                Column("value_a", first.name, first.unit, "number"),
                Column("value_b", second.name, second.unit, "number"),
            ),
            rows=tuple(
                {
                    "observation_date": a.observation_date.isoformat(),
                    "value_a": decimal_text(a.value),
                    "value_b": decimal_text(b.value),
                }
                for a, b in shown
            ),
            as_of=max(left.pit, right.pit),
            source="；".join(dict.fromkeys((first.source, second.source))),
            status="stale" if stale else "ok",
            reason="；".join(stale) or None,
            truncated=len(pairs) - len(shown),
        )
