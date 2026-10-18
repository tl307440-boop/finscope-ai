import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

# 观测日取所属月 / 季首日，所以「观测日距今天数」天然包含了公布滞后：
# 月度数据正常情况下最新一期也会落后 45–90 天（工业企业利润次月 27 日才公布），
# 季度数据（GDP）会落后 200 天以上。阈值按频率取「下一期按时公布之后再留一点余量」。
STALE_DAYS_BY_FREQUENCY: dict[str, int] = {
    "Daily": 14,
    "Weekly": 21,
    "Monthly": 90,
    "Quarterly": 215,
}
# 兼容旧引用：月度阈值。
STALE_DAYS = STALE_DAYS_BY_FREQUENCY["Monthly"]


@dataclass(frozen=True)
class CsvSeriesSpec:
    """中国宏观序列的登记信息。这里只有目录，没有观测值。"""

    series_code: str
    name_cn: str
    name_en: str
    category: str
    frequency: str
    base_unit: str
    seasonal_adjustment: str
    source_agency: str
    # True：不做自动同步，只能由管理员导入正式 CSV，导入前保持 unavailable。
    manual_only: bool = False


def china_series_catalog() -> tuple[CsvSeriesSpec, ...]:
    """序列定义来自国家统计局、中国人民银行、海关总署的统计口径说明。

    这里只登记序列元数据，一条观测值都没有：数值由后台任务从公开接口抓取，
    或（manual_only）由管理员导入正式 CSV。
    """

    return (
        CsvSeriesSpec(
            series_code="CPI_CN",
            name_cn="中国居民消费价格同比",
            name_en="China Consumer Price Index, Year-on-Year",
            category="inflation",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="国家统计局",
        ),
        CsvSeriesSpec(
            series_code="PPI_CN",
            name_cn="中国工业生产者出厂价格同比",
            name_en="China Producer Price Index for Industrial Products, Year-on-Year",
            category="inflation",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="国家统计局",
        ),
        CsvSeriesSpec(
            series_code="PMI_CN",
            name_cn="中国制造业采购经理指数",
            name_en="China Manufacturing Purchasing Managers' Index",
            category="output",
            frequency="Monthly",
            base_unit="Index",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="国家统计局",
        ),
        CsvSeriesSpec(
            series_code="M2_CN",
            name_cn="中国广义货币供应量同比",
            name_en="China Broad Money Supply (M2), Year-on-Year",
            category="money",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="中国人民银行",
        ),
        CsvSeriesSpec(
            series_code="INDUS_CN",
            name_cn="中国规模以上工业增加值同比",
            name_en="China Value Added of Industry above Designated Size, Year-on-Year",
            category="output",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="国家统计局",
        ),
        CsvSeriesSpec(
            series_code="SOCIAL_FINANCING",
            name_cn="社会融资规模",
            name_en="China Aggregate Financing to the Real Economy",
            category="money",
            frequency="Monthly",
            base_unit="亿元",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="中国人民银行",
        ),
        # —— 指标带新增（国家统计局 / 东方财富转载，已用公开接口实测取到数值）——
        _spec(
            "CORE_CPI_CN",
            "中国核心CPI同比（不含食品和能源）",
            "China Core CPI (excl. Food and Energy), Year-on-Year",
            "inflation",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "PMI_NEW_ORDERS_CN",
            "中国制造业PMI新订单指数",
            "China Manufacturing PMI: New Orders Index",
            "sentiment",
            "Monthly",
            "Index",
            "国家统计局",
            adjustment="Seasonally Adjusted",
        ),
        _spec(
            "PMI_NEW_EXPORT_ORDERS_CN",
            "中国制造业PMI新出口订单指数",
            "China Manufacturing PMI: New Export Orders Index",
            "sentiment",
            "Monthly",
            "Index",
            "国家统计局",
            adjustment="Seasonally Adjusted",
        ),
        _spec(
            "INDUS_PROFIT_CN",
            "中国规模以上工业企业利润总额累计同比",
            "China Total Profits of Industrial Enterprises above Designated Size, YTD YoY",
            "output",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "M1_CN",
            "中国狭义货币供应量（M1）同比",
            "China Narrow Money Supply (M1), Year-on-Year",
            "money",
            "Monthly",
            "Percent",
            "中国人民银行",
        ),
        _spec(
            "RETAIL_CN",
            "中国社会消费品零售总额当月同比",
            "China Total Retail Sales of Consumer Goods, Year-on-Year",
            "consumption",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "HOUSE_SALES_CN",
            "中国新建商品房销售额累计同比",
            "China Sales Value of Newly Built Commercial Housing, YTD YoY",
            "housing",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "PRIVATE_INVEST_CN",
            "中国民间固定资产投资累计同比",
            "China Private Fixed Asset Investment, YTD YoY",
            "investment",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "MFG_INVEST_CN",
            "中国制造业固定资产投资累计同比",
            "China Manufacturing Fixed Asset Investment, YTD YoY",
            "investment",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "FISCAL_SPEND_CN",
            "中国一般公共预算支出累计同比",
            "China General Public Budget Expenditure, YTD YoY",
            "fiscal",
            "Monthly",
            "Percent",
            "国家统计局",
        ),
        _spec(
            "GDP_CN",
            "中国实际GDP当季同比",
            "China Real GDP, Quarterly Year-on-Year",
            "output",
            "Quarterly",
            "Percent",
            "国家统计局",
            adjustment="Not Seasonally Adjusted",
        ),
        _spec(
            "EXPORT_CN",
            "中国出口金额当月同比（美元计价）",
            "China Exports, Year-on-Year (USD)",
            "trade",
            "Monthly",
            "Percent",
            "海关总署",
        ),
        # —— 中国人民银行调查统计司统计表：对方 robots.txt 不允许抓取，不做自动同步，
        #    只由管理员按月导入 CSV（seed_data/china_pboc_monthly.csv 为已核对的历史月份）——
        _spec(
            "SOCIAL_FINANCING_STOCK_CN",
            "中国社会融资规模存量同比",
            "China Aggregate Financing Stock, Year-on-Year",
            "money",
            "Monthly",
            "Percent",
            "中国人民银行",
            manual_only=True,
        ),
        _spec(
            "ENT_MLT_LOAN_CN",
            "中国企业中长期贷款增量",
            "China Net New Medium and Long-term Loans to Enterprises",
            "money",
            "Monthly",
            "亿元",
            "中国人民银行",
            manual_only=True,
        ),
        _spec(
            "GOV_BOND_NET_CN",
            "中国社融口径政府债券净融资额",
            "China Net Government Bond Financing (within Aggregate Financing)",
            "money",
            "Monthly",
            "亿元",
            "中国人民银行",
            manual_only=True,
        ),
        # 7 天逆回购利率与 DR007 没有可取得的数据，已从目录中去掉，不登记空序列。
    )


def _spec(
    series_code: str,
    name_cn: str,
    name_en: str,
    category: str,
    frequency: str,
    base_unit: str,
    source_agency: str,
    *,
    adjustment: str = "Not Seasonally Adjusted",
    manual_only: bool = False,
) -> CsvSeriesSpec:
    return CsvSeriesSpec(
        series_code=series_code,
        name_cn=name_cn,
        name_en=name_en,
        category=category,
        frequency=frequency,
        base_unit=base_unit,
        seasonal_adjustment=adjustment,
        source_agency=source_agency,
        manual_only=manual_only,
    )


def series_spec(series_code: str) -> CsvSeriesSpec | None:
    for spec in china_series_catalog():
        if spec.series_code == series_code:
            return spec
    return None


# 兼容既有的三元组视图（code, 中文名, 单位）。序列定义只有 catalog 一处来源。
CHINA_SERIES = tuple(
    (spec.series_code, spec.name_cn, spec.base_unit) for spec in china_series_catalog()
)


@dataclass(frozen=True)
class CsvObservation:
    series_code: str
    observation_date: date
    value: Decimal
    unit: str
    source: str
    published_at: date


def parse_macro_csv(text: str) -> tuple[list[CsvObservation], list[str]]:
    reader = csv.DictReader(StringIO(text))
    required = {"series_code", "observation_date", "value", "unit", "source", "published_at"}
    if reader.fieldnames is None or not required.issubset(set(reader.fieldnames)):
        return [], ["CSV 必须包含来源、单位、观测日和公布日"]
    allowed = {code for code, _name, _unit in CHINA_SERIES}
    accepted: list[CsvObservation] = []
    errors: list[str] = []
    for index, row in enumerate(reader, start=2):
        code = (row.get("series_code") or "").strip()
        source = (row.get("source") or "").strip()
        unit = (row.get("unit") or "").strip()
        if code not in allowed:
            errors.append(f"第 {index} 行不在已登记的中国宏观序列里")
            continue
        if not source or not unit:
            errors.append(f"第 {index} 行缺少来源或单位")
            continue
        try:
            observed = date.fromisoformat((row.get("observation_date") or "").strip())
            published = date.fromisoformat((row.get("published_at") or "").strip())
            value = Decimal((row.get("value") or "").strip())
        except (ValueError, InvalidOperation):
            errors.append(f"第 {index} 行日期或数值无法解析")
            continue
        if published < observed:
            errors.append(f"第 {index} 行的公布日早于观测日")
            continue
        accepted.append(
            CsvObservation(
                series_code=code,
                observation_date=observed,
                value=value,
                unit=unit,
                source=source,
                published_at=published,
            )
        )
    return accepted, errors


def series_status(code: str, stored: bool) -> str:
    """stored 必须来自真实查询：库里没有观测值就只能是 unavailable。"""

    if code == "SOCIAL_FINANCING" and not stored:
        return "unavailable"
    if not stored:
        return "unavailable"
    return "stored"


def is_stale(latest_observation_date: date | None, today: date, frequency: str = "Monthly") -> bool:
    """最新观测日距今天超过该频率的阈值就按 stale 处理，不猜测缺失的期次。"""

    if latest_observation_date is None:
        return False
    limit = STALE_DAYS_BY_FREQUENCY.get(frequency, STALE_DAYS)
    return (today - latest_observation_date).days > limit


def public_status(
    code: str,
    *,
    observation_count: int,
    latest_observation_date: date | None,
    today: date,
) -> str:
    """把「有没有数据」和「数据新不新」合成页面用的三态。"""

    status = series_status(code, observation_count > 0)
    if status == "unavailable":
        return status
    spec = series_spec(code)
    frequency = spec.frequency if spec is not None else "Monthly"
    return "stale" if is_stale(latest_observation_date, today, frequency) else status
