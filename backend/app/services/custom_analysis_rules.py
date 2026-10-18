"""自定义数据分析的纯计算：不碰网络与数据库，输入输出都带单位，可离线复现。

口径速查（与 docs/superpowers/specs/2026-10-18-custom-analysis-design.md 一致）：
- PCR = 认沽量 / 认购量（成交量、持仓量各一套），在同一标的篮子内先加总再相除；
- 分位数 / 通道的窗口是「截至当天的最近 5 个自然年」，样本不足 750 个交易日就不给，
  不用更短的窗口充数；
- 股债收益差 = 100 / PE-TTM − 10 年国债收益率（两边都是百分数）；
- 巴菲特指标 = A 股总市值（元）/ 滚动 4 季 GDP（亿元 × 1e8）× 100%，
  GDP 按「报告期末 + 21 天」之后才视为已公布，不插值、不使用未来数据；
- 全市场中位数 PB：剔除 PB 缺失或 ≤ 0、ST / 退市整理、上市不满 180 天。
"""

from __future__ import annotations

import calendar
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from app.providers.eastmoney_valuation import StockValuation
from app.providers.exchange_options import OptionStat

RULES_VERSION = "custom-analysis-v1"
QualityFlag = Literal["ok", "review", "rejected"]

PCR_FEAR_ABOVE = Decimal("1.2")
PCR_GREED_BELOW = Decimal("0.6")
PCT_HIGH = 90.0
PCT_LOW = 10.0
WINDOW_YEARS = 5
MIN_WINDOW_SAMPLES = 750
MA_WINDOW = 10
GDP_RELEASE_LAG_DAYS = 21
MIN_LISTING_DAYS = 180
MIN_CROSS_SECTION_STOCKS = 3000
_PCT_TOLERANCE = Decimal("0.0101")  # 交易所披露值只给两位小数，允许四舍五入的误差


@dataclass(frozen=True)
class QualityResult:
    flag: QualityFlag
    note: str | None = None


# ---------------------------------------------------------------- 期权 PCR


def put_call_ratio(put: int, call: int) -> Decimal | None:
    """认沽 / 认购；认购为 0 时没有意义，返回 None 而不是 0 或无穷。"""

    if call <= 0 or put < 0:
        return None
    return (Decimal(put) / Decimal(call)).quantize(Decimal("0.000001"))


def check_option_stat(stat: OptionStat) -> QualityResult:
    """入库前的质量闸门：负数 / 全 0 拒收；与交易所自己披露的比值对不上就标待审。"""

    numbers = (stat.call_volume, stat.put_volume, stat.call_oi, stat.put_oi)
    if any(number < 0 for number in numbers):
        return QualityResult("rejected", "数量出现负数")
    if all(number == 0 for number in numbers):
        return QualityResult("rejected", "成交量与持仓量全为 0")
    notes: list[str] = []
    if (
        stat.reported_total_volume is not None
        and stat.reported_total_volume != stat.call_volume + stat.put_volume
    ):
        notes.append(
            f"披露总成交量 {stat.reported_total_volume} ≠ 认购 + 认沽 "
            f"{stat.call_volume + stat.put_volume}"
        )
    if stat.reported_ratio is not None and stat.reported_ratio_kind is not None:
        if stat.reported_ratio_kind == "volume_pcr":
            computed = put_call_ratio(stat.put_volume, stat.call_volume)
        else:
            computed = put_call_ratio(stat.put_oi, stat.call_oi)
        if computed is not None and abs(computed * 100 - stat.reported_ratio) > _PCT_TOLERANCE:
            notes.append(
                f"自算比值 {computed * 100:.2f}% 与交易所披露 {stat.reported_ratio}% 不一致"
            )
    if stat.call_volume == 0:
        notes.append("认购成交量为 0，成交量 PCR 无法计算")
    if notes:
        return QualityResult("review", "；".join(notes)[:255])
    return QualityResult("ok")


def sentiment_signal(volume_pcr: Decimal | None) -> str | None:
    """PRD 的固定阈值：> 1.2 极度恐慌、< 0.6 极度贪婪。阈值是经验线，不是统计结论。"""

    if volume_pcr is None:
        return None
    if volume_pcr > PCR_FEAR_ABOVE:
        return "极度恐慌"
    if volume_pcr < PCR_GREED_BELOW:
        return "极度贪婪"
    return "正常"


def relative_signal(percentile: float | None) -> str | None:
    """按该标的自己的 5 年分位判断：不同标的的 PCR 中枢本来就不同。"""

    if percentile is None:
        return None
    if percentile >= PCT_HIGH:
        return "偏恐慌"
    if percentile <= PCT_LOW:
        return "偏贪婪"
    return "中性"


@dataclass(frozen=True)
class OptionRow:
    trade_date: date
    exchange: str
    underlying_code: str
    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    quality_flag: str


@dataclass(frozen=True)
class Member:
    exchange: str
    code: str
    label: str


@dataclass(frozen=True)
class OptionBasket:
    kind: Literal["etf", "index"]
    label: str
    members: tuple[Member, ...]
    note: str


@dataclass(frozen=True)
class OptionGroup:
    code: str
    name: str
    index_code: str
    index_name: str
    baskets: tuple[OptionBasket, ...]


ETF_NOTE = "沪深两所 ETF 期权的认购 / 认沽量先分别加总，再相除；缺任一成员的交易日不计入。"
INDEX_NOTE = "中金所股指期权（现金交割），与 ETF 期权是不同的合约，不与 ETF 期权混算。"

OPTION_GROUPS: tuple[OptionGroup, ...] = (
    OptionGroup(
        "sse50",
        "上证50",
        "000016",
        "上证50指数",
        (
            OptionBasket(
                "etf", "上证50ETF期权", (Member("SSE", "510050", "上交所 510050"),), ETF_NOTE
            ),
            OptionBasket(
                "index", "上证50股指期权 HO", (Member("CFFEX", "HO", "中金所 HO"),), INDEX_NOTE
            ),
        ),
    ),
    OptionGroup(
        "csi300",
        "沪深300",
        "000300",
        "沪深300指数",
        (
            OptionBasket(
                "etf",
                "沪深300ETF期权",
                (
                    Member("SSE", "510300", "上交所 510300"),
                    Member("SZSE", "159919", "深交所 159919"),
                ),
                ETF_NOTE,
            ),
            OptionBasket(
                "index", "沪深300股指期权 IO", (Member("CFFEX", "IO", "中金所 IO"),), INDEX_NOTE
            ),
        ),
    ),
    OptionGroup(
        "csi500",
        "中证500",
        "000905",
        "中证500指数",
        (
            OptionBasket(
                "etf",
                "中证500ETF期权",
                (
                    Member("SSE", "510500", "上交所 510500"),
                    Member("SZSE", "159922", "深交所 159922"),
                ),
                ETF_NOTE,
            ),
        ),
    ),
    OptionGroup(
        "csi1000",
        "中证1000",
        "000852",
        "中证1000指数",
        (
            OptionBasket(
                "index",
                "中证1000股指期权 MO",
                (Member("CFFEX", "MO", "中金所 MO"),),
                "沪深交易所没有挂牌中证1000ETF期权（512100 仅在上交所全真模拟环境），"
                "这里用中金所 MO；2022-07-22 才上市，历史不足 5 年。",
            ),
        ),
    ),
    OptionGroup(
        "star50",
        "科创50",
        "000688",
        "上证科创板50成份指数",
        (
            OptionBasket(
                "etf",
                "科创50ETF期权",
                (
                    Member("SSE", "588000", "上交所 588000 华夏"),
                    Member("SSE", "588080", "上交所 588080 易方达"),
                ),
                "科创50ETF 期权只在上交所挂牌，华夏 588000 与易方达 588080 两只的认购 / 认沽量"
                "先分别加总再相除；2023-06-05 才上市，历史不足 5 年；"
                "中金所目前没有科创50股指期权（尚在研发），深交所也没有，因此没有股指期权篮子。",
            ),
        ),
    ),
)
GROUPS_BY_CODE: dict[str, OptionGroup] = {group.code: group for group in OPTION_GROUPS}
INDEX_CODES: tuple[str, ...] = tuple(group.index_code for group in OPTION_GROUPS)


@dataclass(frozen=True)
class BasketDay:
    trade_date: date
    call_volume: int
    put_volume: int
    call_oi: int
    put_oi: int
    quality_flag: QualityFlag


def combine_basket(rows: Sequence[OptionRow], basket: OptionBasket) -> tuple[list[BasketDay], int]:
    """把篮子里各成员按交易日加总。缺成员的交易日整天丢弃并计数，绝不拿残缺的和算 PCR。"""

    wanted = {(member.exchange, member.code) for member in basket.members}
    by_day: dict[date, dict[tuple[str, str], OptionRow]] = defaultdict(dict)
    for row in rows:
        key = (row.exchange, row.underlying_code)
        if key in wanted:
            by_day[row.trade_date][key] = row
    days: list[BasketDay] = []
    incomplete = 0
    for day in sorted(by_day):
        present = by_day[day]
        if set(present) != wanted:
            incomplete += 1
            continue
        flags = {row.quality_flag for row in present.values()}
        days.append(
            BasketDay(
                trade_date=day,
                call_volume=sum(row.call_volume for row in present.values()),
                put_volume=sum(row.put_volume for row in present.values()),
                call_oi=sum(row.call_oi for row in present.values()),
                put_oi=sum(row.put_oi for row in present.values()),
                quality_flag="review" if "review" in flags else "ok",
            )
        )
    return days, incomplete


def moving_average(
    values: Sequence[Decimal | None], window: int = MA_WINDOW
) -> list[Decimal | None]:
    """最近 window 个**有值**的交易日均值；不足 window 个或窗口内有缺口就是 None。"""

    out: list[Decimal | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            out.append(None)
            continue
        chunk = values[index + 1 - window : index + 1]
        if any(item is None for item in chunk):
            out.append(None)
            continue
        total = sum((item for item in chunk if item is not None), Decimal(0))
        out.append((total / window).quantize(Decimal("0.000001")))
    return out


# ---------------------------------------------------------------- 滚动窗口统计


def years_back(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 2 月 29 日
        return day.replace(year=day.year - years, day=28)


def _window_start_indices(dates: Sequence[date], years: int) -> list[int]:
    """每个位置对应的窗口起点下标：窗口 = (d - years, d]。"""

    starts: list[int] = []
    lo = 0
    for day in dates:
        cutoff = years_back(day, years)
        while dates[lo] <= cutoff:
            lo += 1
        starts.append(lo)
    return starts


def rolling_percentile(
    dates: Sequence[date],
    values: Sequence[Decimal | float],
    *,
    years: int = WINDOW_YEARS,
    min_samples: int = MIN_WINDOW_SAMPLES,
) -> list[float | None]:
    """当天值在最近 N 年（含当天）里的百分位 = 不大于当天值的样本占比 × 100。"""

    if len(dates) != len(values):
        raise ValueError("dates 与 values 长度不一致")
    out: list[float | None] = []
    for index, lo in enumerate(_window_start_indices(dates, years)):
        window = values[lo : index + 1]
        if len(window) < min_samples:
            out.append(None)
            continue
        current = values[index]
        below = sum(1 for item in window if item <= current)
        out.append(round(below / len(window) * 100, 2))
    return out


@dataclass(frozen=True)
class Band:
    mean: float
    std: float

    @property
    def upper(self) -> float:
        return self.mean + 2 * self.std

    @property
    def lower(self) -> float:
        return self.mean - 2 * self.std


def rolling_band(
    dates: Sequence[date],
    values: Sequence[Decimal | float],
    *,
    years: int = WINDOW_YEARS,
    min_samples: int = MIN_WINDOW_SAMPLES,
) -> list[Band | None]:
    """最近 N 年的均值与样本标准差（n − 1），供 μ ± 2σ 通道使用。"""

    if len(dates) != len(values):
        raise ValueError("dates 与 values 长度不一致")
    out: list[Band | None] = []
    for index, lo in enumerate(_window_start_indices(dates, years)):
        window = [float(item) for item in values[lo : index + 1]]
        if len(window) < min_samples or len(window) < 2:
            out.append(None)
            continue
        out.append(Band(mean=statistics.fmean(window), std=statistics.stdev(window)))
    return out


# ---------------------------------------------------------------- 股债收益差


def earnings_yield_pct(pe_ttm: Decimal) -> Decimal | None:
    if pe_ttm <= 0:
        return None
    return Decimal(100) / pe_ttm


def equity_risk_premium_pct(pe_ttm: Decimal, yield_10y_pct: Decimal) -> Decimal | None:
    """ERP（百分点）= 100 / PE-TTM − 10 年国债收益率(%)。两边单位都是 %，不会混用小数与百分数。"""

    earnings = earnings_yield_pct(pe_ttm)
    if earnings is None:
        return None
    return earnings - yield_10y_pct


# ---------------------------------------------------------------- 巴菲特指标


def _shift_quarter_end(day: date, quarters: int) -> date:
    total = day.year * 12 + (day.month - 1) - quarters * 3
    year, month = divmod(total, 12)
    return date(year, month + 1, calendar.monthrange(year, month + 1)[1])


def single_quarter_gdp(cumulative: Mapping[date, Decimal]) -> dict[date, Decimal]:
    """年内累计 → 单季值：Q1 = 累计；其余 = 本期累计 − 上一期累计。上一期缺失就不出这一季。"""

    singles: dict[date, Decimal] = {}
    for quarter_end, value in cumulative.items():
        if quarter_end.month == 3:
            singles[quarter_end] = value
            continue
        previous = cumulative.get(_shift_quarter_end(quarter_end, 1))
        if previous is not None and previous.is_finite():
            singles[quarter_end] = value - previous
    return singles


def trailing_gdp_billion(
    singles: Mapping[date, Decimal], as_of: date, *, lag_days: int = GDP_RELEASE_LAG_DAYS
) -> tuple[Decimal, date] | None:
    """as_of 当天已公布的最近 4 个连续单季 GDP 之和（亿元），并返回最新一季的季末日。

    统计局一般在季末后 15–20 天公布，所以报告期末 + 21 天之前不算已知；
    4 个季度里缺任何一个就返回 None，不拿 3 个季度外推。
    """

    known = [q for q in singles if q + timedelta(days=lag_days) <= as_of]
    if not known:
        return None
    latest = max(known)
    quarters = [_shift_quarter_end(latest, step) for step in range(4)]
    if any(q not in singles for q in quarters):
        return None
    return sum((singles[q] for q in quarters), Decimal(0)), latest


def buffett_ratio_pct(
    total_market_cap_cny: Decimal, trailing_gdp_billion_cny: Decimal
) -> Decimal | None:
    """总市值（元）/ 滚动 4 季 GDP（亿元 → 元）× 100。"""

    if trailing_gdp_billion_cny <= 0:
        return None
    return total_market_cap_cny / (trailing_gdp_billion_cny * Decimal(10**8)) * 100


# ---------------------------------------------------------------- 全市场中位数 PB


def is_st_name(name: str) -> bool:
    compact = name.replace(" ", "").upper()
    return (
        compact.startswith(("ST", "*ST", "S*ST", "SST"))
        or compact.endswith("退")
        or "退市" in compact
    )


@dataclass(frozen=True)
class MedianPb:
    median: Decimal | None
    median_without_st_filter: Decimal | None
    universe: int
    sample: int
    excluded_missing: int
    excluded_negative: int
    excluded_new: int
    excluded_st: int
    listing_unknown: int


def compute_median_pb(
    rows: Sequence[StockValuation], day: date, listing_dates: Mapping[str, date]
) -> MedianPb:
    """按 PRD 的清洗规则求中位数。

    `median_without_st_filter` 是不剔除 ST 的对照值：历史回填时 ST 只能按**当前**简称判断，
    读者可以拿两条线的差距判断这个近似有多大。
    """

    missing = negative = new = st = unknown = 0
    kept_with_st: list[Decimal] = []
    kept: list[Decimal] = []
    for row in rows:
        if row.pb_mrq is None:
            missing += 1
            continue
        if row.pb_mrq <= 0:
            negative += 1
            continue
        listed = listing_dates.get(row.code)
        if listed is None:
            unknown += 1
        elif (day - listed).days < MIN_LISTING_DAYS:
            new += 1
            continue
        kept_with_st.append(row.pb_mrq)
        if is_st_name(row.name):
            st += 1
            continue
        kept.append(row.pb_mrq)
    return MedianPb(
        median=Decimal(statistics.median(kept)) if kept else None,
        median_without_st_filter=Decimal(statistics.median(kept_with_st)) if kept_with_st else None,
        universe=len(rows),
        sample=len(kept),
        excluded_missing=missing,
        excluded_negative=negative,
        excluded_new=new,
        excluded_st=st,
        listing_unknown=unknown,
    )


# ---------------------------------------------------------------- 序列级质量闸门


def check_cross_section(rows: Sequence[StockValuation], expected_count: int) -> QualityResult:
    """全市场截面必须取全：条数对不上或明显偏少，说明分页中途失败，整天作废。"""

    if len(rows) < MIN_CROSS_SECTION_STOCKS:
        return QualityResult(
            "rejected", f"截面只有 {len(rows)} 只，少于 {MIN_CROSS_SECTION_STOCKS}"
        )
    if expected_count and len(rows) != expected_count:
        return QualityResult("rejected", f"截面条数 {len(rows)} ≠ 接口声明 {expected_count}")
    with_cap = sum(
        1 for row in rows if row.total_market_cap is not None and row.total_market_cap > 0
    )
    if with_cap < len(rows) * Decimal("0.95"):
        return QualityResult("review", f"仅 {with_cap}/{len(rows)} 只有总市值")
    return QualityResult("ok")


def check_pe(pe_ttm: Decimal) -> QualityResult:
    if pe_ttm <= 0 or pe_ttm > 200:
        return QualityResult("rejected", f"PE-TTM {pe_ttm} 超出合理范围 (0, 200]")
    return QualityResult("ok")


def check_yield(yield_pct: Decimal) -> QualityResult:
    if yield_pct <= 0 or yield_pct > 15:
        return QualityResult("rejected", f"10 年国债收益率 {yield_pct}% 超出合理范围 (0, 15]")
    return QualityResult("ok")


def check_jump(
    previous: Decimal | None, current: Decimal, limit: Decimal, label: str
) -> QualityResult:
    """相邻两个交易日变化超过 limit（比例）就标待审，值照常保留供人工复核。"""

    if previous is None or previous == 0:
        return QualityResult("ok")
    change = abs(current / previous - 1)
    if change > limit:
        return QualityResult(
            "review", f"{label}单日变化 {change * 100:.1f}% 超过 {limit * 100:.0f}%"
        )
    return QualityResult("ok")
