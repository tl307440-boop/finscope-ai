import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

CHINA_SERIES = (
    ("CPI_CN", "中国居民消费价格同比", "Percent"),
    ("PPI_CN", "中国工业生产者出厂价格同比", "Percent"),
    ("PMI_CN", "中国制造业采购经理指数", "Index"),
    ("M2_CN", "中国广义货币供应量同比", "Percent"),
    ("INDUS_CN", "中国规模以上工业增加值同比", "Percent"),
    ("SOCIAL_FINANCING", "社会融资规模", "CNY"),
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
    if code == "SOCIAL_FINANCING" and not stored:
        return "unavailable"
    if not stored:
        return "unavailable"
    return "stored"
