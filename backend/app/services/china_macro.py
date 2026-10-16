import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

STALE_DAYS = 60


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


def china_series_catalog() -> tuple[CsvSeriesSpec, ...]:
    """六个序列的定义都来自国家统计局/中国人民银行的统计口径说明。

    这里只登记序列元数据，一条观测值都没有：数值只能由正式来源 CSV 导入。
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
            base_unit="CNY",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="中国人民银行",
        ),
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


def is_stale(latest_observation_date: date | None, today: date) -> bool:
    """最新观测日距今天超过 STALE_DAYS 就按 stale 处理，不猜测缺失的月份值。"""

    if latest_observation_date is None:
        return False
    return (today - latest_observation_date).days > STALE_DAYS


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
    return "stale" if is_stale(latest_observation_date, today) else status
