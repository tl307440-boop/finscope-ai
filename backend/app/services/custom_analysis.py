"""自定义数据分析的读取侧：从入库数据算出 PCR 时序、三项估值指标和数据源状态。

所有计算都复用 custom_analysis_rules 里的纯函数；这里只负责取数、对齐日期、
拼装响应，并把「缺数据 / 样本不足 / 已过期」如实写进响应，不用估算值补洞。
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol

from app.repositories.custom_analysis import (
    OptionSummary,
    SeriesSummary,
    StoredPoint,
)
from app.schemas.custom_analysis import (
    IndicatorCode,
    IndicatorPoint,
    IndicatorView,
    InputStatus,
    PcrBasketView,
    PcrPoint,
    PcrView,
    QualityIssue,
    SourceRow,
    SourcesView,
    Status,
    ValuationView,
)
from app.services.custom_analysis_rules import (
    GROUPS_BY_CODE,
    MIN_WINDOW_SAMPLES,
    OPTION_GROUPS,
    PCR_FEAR_ABOVE,
    PCR_GREED_BELOW,
    RULES_VERSION,
    WINDOW_YEARS,
    OptionBasket,
    OptionGroup,
    OptionRow,
    buffett_ratio_pct,
    combine_basket,
    earnings_yield_pct,
    equity_risk_premium_pct,
    moving_average,
    put_call_ratio,
    relative_signal,
    rolling_band,
    rolling_percentile,
    sentiment_signal,
    single_quarter_gdp,
    trailing_gdp_billion,
)
from app.services.custom_analysis_sync import (
    CALENDAR_INDEX,
    SERIES_GDP,
    SERIES_MV,
    SERIES_PB,
    SERIES_PB_ALL,
    SERIES_YIELD,
    index_close_series,
    index_pe_series,
)

STALE_DAYS = 6
GDP_STALE_DAYS = 200
DEFAULT_PCR_DAYS = 750
DEFAULT_VALUATION_DAYS = 1300
PCT_OVERHEATED = 80.0
PCT_CHEAP = 20.0


class Reader(Protocol):
    def option_rows(
        self, members: Sequence[tuple[str, str]], start: date | None = None
    ) -> list[OptionRow]: ...

    def option_summaries(self) -> list[OptionSummary]: ...

    def option_review_notes(self, limit: int = 20) -> list[tuple[date, str, str, str]]: ...

    def series(self, series_code: str, start: date | None = None) -> list[StoredPoint]: ...

    def series_summaries(self) -> dict[str, SeriesSummary]: ...

    def series_review_notes(self, limit: int = 20) -> list[tuple[date, str, str]]: ...


def _f(value: Decimal | float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(float(value), digits)


def _worst(*flags: str) -> str:
    return "review" if any(flag != "ok" for flag in flags) else "ok"


@dataclass
class CustomAnalysisService:
    store: Reader
    today: Callable[[], date]

    # ------------------------------------------------------------------ PCR
    def pcr(self, group_code: str, days: int = DEFAULT_PCR_DAYS) -> PcrView | None:
        group = GROUPS_BY_CODE.get(group_code)
        if group is None:
            return None
        closes = {
            point.trade_date: point.value
            for point in self.store.series(index_close_series(group.index_code))
        }
        return PcrView(
            group_code=group.code,
            group_name=group.name,
            index_code=group.index_code,
            index_name=group.index_name,
            baskets=[self._basket(basket, closes, days) for basket in group.baskets],
            rules=[
                "PCR = 认沽量 / 认购量；成交量 PCR 与持仓量 PCR 各算一套，先在篮子内加总再相除。",
                f"固定阈值（PRD 经验线）：成交量 PCR > {PCR_FEAR_ABOVE} 为极度恐慌，"
                f"< {PCR_GREED_BELOW} 为极度贪婪；这只是参考线，不是统计结论。",
                f"相对信号：用该标的自己最近 {WINDOW_YEARS} 年的分位，"
                "≥ 90 分位偏恐慌，≤ 10 分位偏贪婪；"
                f"样本不足 {MIN_WINDOW_SAMPLES} 个交易日时不给分位。",
                "ETF 期权与中金所股指期权是不同合约，分开展示，不混算。",
            ],
            rules_version=RULES_VERSION,
        )

    def _basket(
        self, basket: OptionBasket, closes: dict[date, Decimal], days: int
    ) -> PcrBasketView:
        members = [(member.exchange, member.code) for member in basket.members]
        base = PcrBasketView(
            kind=basket.kind,
            label=basket.label,
            members=[member.label for member in basket.members],
            note=basket.note,
            status="unavailable",
        )
        basket_days, incomplete = combine_basket(self.store.option_rows(members), basket)
        if not basket_days:
            base.reason = "还没有同步到这个篮子的交易日数据，请先运行数据同步"
            base.incomplete_days = incomplete
            return base
        dates = [item.trade_date for item in basket_days]
        volume_pcr = [put_call_ratio(item.put_volume, item.call_volume) for item in basket_days]
        oi_pcr = [put_call_ratio(item.put_oi, item.call_oi) for item in basket_days]
        averages = moving_average(volume_pcr)
        valid = [
            (day, value) for day, value in zip(dates, volume_pcr, strict=True) if value is not None
        ]
        percentiles = dict(
            zip(
                [day for day, _ in valid],
                rolling_percentile([day for day, _ in valid], [value for _, value in valid]),
                strict=True,
            )
        )
        points: list[PcrPoint] = []
        for index, item in enumerate(basket_days):
            close = closes.get(item.trade_date)
            percentile = percentiles.get(item.trade_date)
            points.append(
                PcrPoint(
                    trade_date=item.trade_date,
                    index_close=_f(close, 2),
                    call_volume=item.call_volume,
                    put_volume=item.put_volume,
                    call_oi=item.call_oi,
                    put_oi=item.put_oi,
                    volume_pcr=_f(volume_pcr[index], 4),
                    volume_pcr_ma10=_f(averages[index], 4),
                    oi_pcr=_f(oi_pcr[index], 4),
                    volume_pcr_percentile_5y=percentile,
                    sentiment_signal=sentiment_signal(volume_pcr[index]),
                    relative_signal=relative_signal(percentile),
                    quality_flag=item.quality_flag,
                )
            )
        last = points[-1]
        stale = self._is_stale(last.trade_date, STALE_DAYS)
        reasons: list[str] = []
        if stale:
            reasons.append(f"最近交易日数据是 {last.trade_date}，已过期，请检查同步任务")
        if last.volume_pcr_percentile_5y is None:
            reasons.append(
                f"有效样本 {len(valid)} 个，不足 {MIN_WINDOW_SAMPLES} 个，"
                f"暂不计算 {WINDOW_YEARS} 年分位"
            )
        status: Status = "stale" if stale else "ok"
        return PcrBasketView(
            kind=basket.kind,
            label=basket.label,
            members=[member.label for member in basket.members],
            note=basket.note,
            status=status,
            reason="；".join(reasons) or None,
            first_date=dates[0],
            last_date=dates[-1],
            history_years=round((dates[-1] - dates[0]).days / 365.25, 2),
            incomplete_days=incomplete,
            latest=last,
            points=points[-days:],
        )

    # ------------------------------------------------------------------ 估值
    def valuation(self, days: int = DEFAULT_VALUATION_DAYS) -> ValuationView:
        summaries = self.store.series_summaries()
        return ValuationView(
            indicators=[
                self._erp(days, summaries),
                self._buffett(days, summaries),
                self._median_pb(days, summaries),
            ],
            rules_version=RULES_VERSION,
        )

    def _inputs(
        self, summaries: dict[str, SeriesSummary], names: Sequence[tuple[str, str]]
    ) -> list[InputStatus]:
        out: list[InputStatus] = []
        for code, label in names:
            summary = summaries.get(code)
            out.append(
                InputStatus(
                    name=label,
                    source=summary.source if summary else "—",
                    last_date=summary.last_date if summary else None,
                    rows=summary.rows if summary else 0,
                )
            )
        return out

    def _unavailable(
        self,
        code: IndicatorCode,
        name: str,
        unit: str,
        definition: str,
        reason: str,
        inputs: list[InputStatus],
        notes: list[str],
    ) -> IndicatorView:
        return IndicatorView(
            code=code,
            name=name,
            unit=unit,
            definition=definition,
            status="unavailable",
            reason=reason,
            inputs=inputs,
            notes=notes,
        )

    def _erp(self, days: int, summaries: dict[str, SeriesSummary]) -> IndicatorView:
        name, unit = "股债收益差 ERP", "百分点"
        definition = (
            "ERP = 100 / 沪深300 PE-TTM − 中国 10 年期国债收益率(%)；通道为最近 5 年均值 ± 2σ。"
        )
        inputs = self._inputs(
            summaries,
            [
                (index_pe_series(CALENDAR_INDEX), "沪深300 PE-TTM"),
                (SERIES_YIELD, "10 年期国债收益率"),
            ],
        )
        notes = [
            "PE-TTM 取中证指数公司披露的沪深300 滚动市盈率；国债收益率取东方财富数据中心转引值。",
            "ERP 越高代表股票相对债券越便宜；高于 μ+2σ 是机会极值区，低于 μ−2σ 是风险警戒区。",
            "两个输入必须同一交易日都有值才计算当天的 ERP。",
        ]
        pe = {p.trade_date: p for p in self.store.series(index_pe_series(CALENDAR_INDEX))}
        yields = {p.trade_date: p for p in self.store.series(SERIES_YIELD)}
        dates = sorted(set(pe) & set(yields))
        rows = [
            (day, pe[day], yields[day], equity_risk_premium_pct(pe[day].value, yields[day].value))
            for day in dates
        ]
        rows = [row for row in rows if row[3] is not None]
        if not rows:
            return self._unavailable(
                "erp", name, unit, definition, "沪深300 PE 与国债收益率没有同日数据", inputs, notes
            )
        series_dates = [row[0] for row in rows]
        values = [row[3] for row in rows if row[3] is not None]
        percentiles = rolling_percentile(series_dates, values)
        bands = rolling_band(series_dates, values)
        points: list[IndicatorPoint] = []
        for index, (day, pe_point, yield_point, erp) in enumerate(rows):
            band = bands[index]
            points.append(
                IndicatorPoint(
                    trade_date=day,
                    value=_f(erp, 4) or 0.0,
                    percentile_5y=percentiles[index],
                    band_mean=_f(band.mean, 4) if band else None,
                    band_upper=_f(band.upper, 4) if band else None,
                    band_lower=_f(band.lower, 4) if band else None,
                    quality_flag=_worst(pe_point.quality_flag, yield_point.quality_flag),
                    extras={
                        "pe_ttm": _f(pe_point.value, 2),
                        "yield_10y_pct": _f(yield_point.value, 4),
                        "earnings_yield_pct": _f(earnings_yield_pct(pe_point.value), 4),
                    },
                )
            )
        latest = points[-1]
        zone = None
        if latest.band_upper is not None and latest.band_lower is not None:
            if latest.value >= latest.band_upper:
                zone = "机会极值区（≥ μ+2σ）"
            elif latest.value <= latest.band_lower:
                zone = "风险警戒区（≤ μ−2σ）"
            else:
                zone = "通道内"
        return self._finish("erp", name, unit, definition, points, days, zone, inputs, notes)

    def _buffett(self, days: int, summaries: dict[str, SeriesSummary]) -> IndicatorView:
        name, unit = "巴菲特指标（A 股修正版）", "%"
        definition = "A 股总市值 / 滚动 4 季 GDP × 100%；GDP 在报告期末 + 21 天后才视为已公布。"
        inputs = self._inputs(
            summaries, [(SERIES_MV, "A 股总市值"), (SERIES_GDP, "GDP 年内累计值")]
        )
        notes = [
            "总市值含沪深北三市 A 股（东方财富全市场截面逐日加总）；",
            "GDP 来自国家统计局，经东方财富转引。",
            "GDP 是年内累计值，已先差分成单季再滚动加总 4 个季度；不做插值，避免用到未来数据。",
            "GDP 取目前最新（含修订）数值，历史季度若被修订，早期读数与当时公布值会有小差异。",
        ]
        cumulative = {p.trade_date: p.value for p in self.store.series(SERIES_GDP)}
        singles = single_quarter_gdp(cumulative)
        market_values = self.store.series(SERIES_MV)
        if not cumulative or not market_values:
            return self._unavailable(
                "buffett",
                name,
                unit,
                definition,
                "缺少 A 股总市值或 GDP 数据，请先运行数据同步",
                inputs,
                notes,
            )
        rows: list[tuple[date, StoredPoint, Decimal, date, Decimal]] = []
        for point in market_values:
            trailing = trailing_gdp_billion(singles, point.trade_date)
            if trailing is None:
                continue
            ratio = buffett_ratio_pct(point.value, trailing[0])
            if ratio is not None:
                rows.append((point.trade_date, point, ratio, trailing[1], trailing[0]))
        if not rows:
            return self._unavailable(
                "buffett",
                name,
                unit,
                definition,
                "已有市值日期早于首个可用的滚动 4 季 GDP",
                inputs,
                notes,
            )
        series_dates = [row[0] for row in rows]
        values = [row[2] for row in rows]
        percentiles = rolling_percentile(series_dates, values)
        points = [
            IndicatorPoint(
                trade_date=day,
                value=_f(ratio, 2) or 0.0,
                percentile_5y=percentiles[index],
                quality_flag=point.quality_flag,
                extras={
                    "total_market_cap_trillion": _f(point.value / Decimal(10**12), 2),
                    "trailing_gdp_trillion": _f(gdp / Decimal(10**4), 2),
                    "gdp_quarter_end": quarter_end.isoformat(),
                    "stock_count": _meta_int(point.meta, "stock_count"),
                },
            )
            for index, (day, point, ratio, quarter_end, gdp) in enumerate(rows)
        ]
        latest = points[-1]
        zone = _percentile_zone(
            latest.percentile_5y, hot="过热区（> 80 分位）", cold="低估区（< 20 分位）"
        )
        return self._finish("buffett", name, unit, definition, points, days, zone, inputs, notes)

    def _median_pb(self, days: int, summaries: dict[str, SeriesSummary]) -> IndicatorView:
        name, unit = "全市场中位数 PB", "倍"
        definition = (
            "全 A 个股 PB(MRQ) 的中位数；剔除 PB 缺失或 ≤ 0、ST / 退市整理、上市不满 180 天。"
        )
        inputs = self._inputs(summaries, [(SERIES_PB, "全市场截面（PB 中位数）")])
        notes = [
            "用中位数而不是均值，避免微盘股极端值扭曲整体水位。",
            "ST 判定按股票简称：当天或前一天抓到的是当时简称；更早的历史日期只能按当前简称近似，"
            "页面同时给出「不剔除 ST」的对照值，两条线差距就是这个近似的上限。",
        ]
        points_raw = self.store.series(SERIES_PB)
        if not points_raw:
            return self._unavailable(
                "median_pb",
                name,
                unit,
                definition,
                "还没有同步全市场截面，请先运行数据同步",
                inputs,
                notes,
            )
        without_st = {p.trade_date: p.value for p in self.store.series(SERIES_PB_ALL)}
        series_dates = [p.trade_date for p in points_raw]
        percentiles = rolling_percentile(series_dates, [p.value for p in points_raw])
        points = [
            IndicatorPoint(
                trade_date=point.trade_date,
                value=_f(point.value, 4) or 0.0,
                percentile_5y=percentiles[index],
                quality_flag=point.quality_flag,
                extras={
                    "median_without_st_filter": _f(without_st.get(point.trade_date), 4),
                    "sample": _meta_int(point.meta, "sample"),
                    "universe": _meta_int(point.meta, "universe"),
                    "excluded_negative": _meta_int(point.meta, "excluded_negative"),
                    "excluded_st": _meta_int(point.meta, "excluded_st"),
                    "excluded_new": _meta_int(point.meta, "excluded_new"),
                    "listing_unknown": _meta_int(point.meta, "listing_unknown"),
                    "st_basis": str((point.meta or {}).get("st_basis") or ""),
                },
            )
            for index, point in enumerate(points_raw)
        ]
        latest = points[-1]
        zone = _percentile_zone(
            latest.percentile_5y, hot="偏贵区（> 80 分位）", cold="低估区（< 20 分位）"
        )
        return self._finish("median_pb", name, unit, definition, points, days, zone, inputs, notes)

    def _finish(
        self,
        code: IndicatorCode,
        name: str,
        unit: str,
        definition: str,
        points: list[IndicatorPoint],
        days: int,
        zone: str | None,
        inputs: list[InputStatus],
        notes: list[str],
    ) -> IndicatorView:
        latest = points[-1]
        stale = self._is_stale(latest.trade_date, STALE_DAYS)
        reasons: list[str] = []
        if stale:
            reasons.append(f"最近数据是 {latest.trade_date}，已过期，请检查同步任务")
        percentile_note = None
        if latest.percentile_5y is None:
            percentile_note = (
                f"有效样本 {len(points)} 个，不足 {MIN_WINDOW_SAMPLES} 个交易日，"
                f"暂不计算 {WINDOW_YEARS} 年分位与通道"
            )
        return IndicatorView(
            code=code,
            name=name,
            unit=unit,
            definition=definition,
            status="stale" if stale else "ok",
            reason="；".join(reasons) or None,
            zone=zone,
            percentile_note=percentile_note,
            latest=latest,
            points=points[-days:],
            inputs=inputs,
            notes=notes,
        )

    # ------------------------------------------------------------------ 数据源
    def sources(self) -> SourcesView:
        series = self.store.series_summaries()
        option_by_exchange = {item.exchange: item for item in self.store.option_summaries()}
        today = self.today()
        rows: list[SourceRow] = []

        def option_row(exchange: str, label: str, provider: str, url: str, note: str) -> None:
            summary = option_by_exchange.get(exchange)
            rows.append(
                _source_row(
                    exchange, label, provider, url, "日频", summary, today, STALE_DAYS, note
                )
            )

        option_row(
            "SSE",
            "上交所期权每日统计",
            "上海证券交易所",
            "http://query.sse.com.cn/commonQuery.do（COMMON_SSE_ZQPZ_YSP_QQ_SJTJ_MRTJ_CX）",
            "官方披露成交量 PCR（CP_RATE），入库时与自算值对账。",
        )
        option_row(
            "SZSE",
            "深交所期权日度概况",
            "深圳证券交易所",
            "https://www.szse.cn/api/report/ShowReport/data（CATALOGID=ysprdzb）",
            "官方披露持仓量 PCR（rcrpccb），入库时与自算值对账；总成交量同样对账。",
        )
        option_row(
            "CFFEX",
            "中金所日统计 CSV（HO / IO / MO 期权）",
            "中国金融期货交易所",
            "http://www.cffex.com.cn/sj/hqsj/rtj/{YYYYMM}/{DD}/{YYYYMMDD}_1.csv",
            "逐合约成交量与持仓量，按品种、认购 / 认沽加总。",
        )
        for code, label, provider, url, frequency, stale_days, note in _SERIES_SOURCES:
            rows.append(
                _source_row(
                    code, label, provider, url, frequency, series.get(code), today, stale_days, note
                )
            )
        issues = [
            QualityIssue(trade_date=day, subject=f"{exchange} {code}", note=note)
            for day, exchange, code, note in self.store.option_review_notes()
        ] + [
            QualityIssue(trade_date=day, subject=code, note=note)
            for day, code, note in self.store.series_review_notes()
        ]
        issues.sort(key=lambda item: item.trade_date, reverse=True)
        return SourcesView(sources=rows, recent_issues=issues[:20], rules_version=RULES_VERSION)

    def _is_stale(self, last: date, limit_days: int) -> bool:
        return (self.today() - last) > timedelta(days=limit_days)


_SERIES_SOURCES: tuple[tuple[str, str, str, str, str, int, str], ...] = (
    (
        index_pe_series(CALENDAR_INDEX),
        "沪深300 滚动市盈率 PE-TTM",
        "中证指数有限公司",
        "https://www.csindex.com.cn/csindex-home/perf/index-perf",
        "日频",
        STALE_DAYS,
        "官方指数行情接口的滚动市盈率字段；同时作为 A 股交易日历。",
    ),
    (
        index_close_series(CALENDAR_INDEX),
        "标的指数收盘（上证50 / 沪深300 / 中证500 / 中证1000 / 科创50）",
        "中证指数有限公司",
        "https://www.csindex.com.cn/csindex-home/perf/index-perf",
        "日频",
        STALE_DAYS,
        "用作 PCR 图上的价格参照；展示的是标的指数而不是 ETF 收盘价。",
    ),
    (
        SERIES_YIELD,
        "中国 10 年期国债收益率",
        "东方财富数据中心（转引）",
        "https://datacenter-web.eastmoney.com/api/data/v1/get（RPTA_WEB_TREASURYYIELD）",
        "日频",
        STALE_DAYS,
        "尚未与中债网公布值逐日对账，页面按东方财富口径展示。",
    ),
    (
        SERIES_GDP,
        "GDP 年内累计值（亿元）",
        "国家统计局（东方财富转引）",
        "https://datacenter-web.eastmoney.com/api/data/v1/get（RPT_ECONOMY_GDP）",
        "季频",
        GDP_STALE_DAYS,
        "累计值已差分为单季；季末 + 21 天后才视为已公布。",
    ),
    (
        SERIES_MV,
        "A 股总市值（全市场逐日加总）",
        "东方财富数据中心",
        "https://datacenter-web.eastmoney.com/api/data/v1/get（RPT_VALUEANALYSIS_DET）",
        "日频",
        STALE_DAYS,
        "取全市场当日截面（含当日在市股票，无幸存者偏差），条数不足 3000 的截面整天作废。",
    ),
    (
        SERIES_PB,
        "全市场中位数 PB",
        "东方财富数据中心",
        "https://datacenter-web.eastmoney.com/api/data/v1/get（RPT_VALUEANALYSIS_DET）",
        "日频",
        STALE_DAYS,
        "历史日期的 ST 剔除按当前简称近似，见各点 st_basis。",
    ),
)


def _source_row(
    key: str,
    label: str,
    provider: str,
    url: str,
    frequency: str,
    summary: SeriesSummary | OptionSummary | None,
    today: date,
    stale_days: int,
    note: str,
) -> SourceRow:
    if summary is None:
        return SourceRow(
            key=key,
            label=label,
            provider=provider,
            url=url,
            frequency=frequency,
            status="unavailable",
            note=note,
        )
    stale = (today - summary.last_date) > timedelta(days=stale_days)
    status: Status = "stale" if stale else "ok"
    return SourceRow(
        key=key,
        label=label,
        provider=provider,
        url=url,
        frequency=frequency,
        status=status,
        rows=summary.rows,
        first_date=summary.first_date,
        last_date=summary.last_date,
        review_rows=summary.review_rows,
        fetched_at=summary.fetched_at,
        note=note,
    )


def _percentile_zone(percentile: float | None, *, hot: str, cold: str) -> str | None:
    if percentile is None:
        return None
    if percentile > PCT_OVERHEATED:
        return hot
    if percentile < PCT_CHEAP:
        return cold
    return "中性区间"


def _meta_int(meta: dict[str, object] | None, key: str) -> int | None:
    value = (meta or {}).get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# ---------------------------------------------------------------------- CSV 导出
PCR_CSV_COLUMNS = (
    "trade_date",
    "underlying_code",
    "underlying_close",
    "put_volume_sum",
    "call_volume_sum",
    "volume_pcr",
    "volume_pcr_ma10",
    "oi_pcr",
    "sentiment_signal",
    "put_oi_sum",
    "call_oi_sum",
    "quality_flag",
)
VALUATION_CSV_COLUMNS = (
    "trade_date",
    "csi300_pe_ttm",
    "cn_10yr_yield",
    "erp_value",
    "erp_percentile_5y",
    "buffett_indicator",
    "buffett_percentile_5y",
    "market_median_pb",
    "market_pb_percentile_5y",
)


def pcr_csv(view: PcrView, kind: str) -> str | None:
    """按 PRD 的 pcr_monitor 结构导出；underlying_close 是标的指数收盘价（官方指数行情）。"""

    basket = next((item for item in view.baskets if item.kind == kind), None)
    if basket is None:
        return None
    code = "+".join(label.split()[-1] for label in basket.members)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(PCR_CSV_COLUMNS)
    for point in basket.points:
        writer.writerow(
            [
                point.trade_date.isoformat(),
                code,
                _cell(point.index_close),
                point.put_volume,
                point.call_volume,
                _cell(point.volume_pcr),
                _cell(point.volume_pcr_ma10),
                _cell(point.oi_pcr),
                point.sentiment_signal or "",
                point.put_oi,
                point.call_oi,
                point.quality_flag,
            ]
        )
    return buffer.getvalue()


def valuation_csv(view: ValuationView) -> str:
    """按 PRD 的 macro_valuation_summary 结构导出：按交易日外连接，缺值留空，不补。"""

    by_code: dict[str, IndicatorView] = {item.code: item for item in view.indicators}
    table: dict[date, dict[str, float | None]] = {}

    def put(code: str, column: str, picker: Callable[[IndicatorPoint], float | None]) -> None:
        indicator = by_code.get(code)
        if indicator is None:
            return
        for point in indicator.points:
            table.setdefault(point.trade_date, {})[column] = picker(point)

    def extra(key: str) -> Callable[[IndicatorPoint], float | None]:
        def pick(point: IndicatorPoint) -> float | None:
            value = point.extras.get(key)
            return float(value) if isinstance(value, int | float) else None

        return pick

    put("erp", "csi300_pe_ttm", extra("pe_ttm"))
    put("erp", "cn_10yr_yield", extra("yield_10y_pct"))
    put("erp", "erp_value", lambda point: point.value)
    put("erp", "erp_percentile_5y", lambda point: point.percentile_5y)
    put("buffett", "buffett_indicator", lambda point: point.value)
    put("buffett", "buffett_percentile_5y", lambda point: point.percentile_5y)
    put("median_pb", "market_median_pb", lambda point: point.value)
    put("median_pb", "market_pb_percentile_5y", lambda point: point.percentile_5y)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(VALUATION_CSV_COLUMNS)
    for day in sorted(table):
        row = table[day]
        writer.writerow(
            [day.isoformat(), *[_cell(row.get(column)) for column in VALUATION_CSV_COLUMNS[1:]]]
        )
    return buffer.getvalue()


def _cell(value: float | None) -> str:
    return "" if value is None else repr(value)


__all__ = [
    "OPTION_GROUPS",
    "CustomAnalysisService",
    "OptionGroup",
    "pcr_csv",
    "valuation_csv",
]
