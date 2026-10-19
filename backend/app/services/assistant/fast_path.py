"""常见问题直取数据（规格 §6 快速路径）。纯函数，只做确定匹配。

问题里出现某条序列的中文名 / 别名、6 位证券代码、自定义指标名时，直接按默认参数列出要调的工具，
少等一轮模型。**匹配不确定就不走**（比如没说中美的「PMI」、同时对上多个板块的 PCR），
交给模型去选，绝不猜。最多预取 3 个。
"""

import re
from dataclasses import dataclass, field
from typing import Any

from app.services.custom_analysis_rules import OPTION_GROUPS

MAX_PREFETCH = 3
_CODE = re.compile(r"(?<!\d)\d{6}(?!\d)")
_CN = ("中国", "国内", "A股")
_US = ("美国", "美联储", "美债")


@dataclass(frozen=True)
class PlannedCall:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class _Alias:
    code: str
    names: tuple[str, ...]
    needs: tuple[str, ...] = ()  # 非空时，问题里还要出现其中之一
    forbids: tuple[str, ...] = ()  # 出现其中之一就不匹配


_ALIASES: tuple[_Alias, ...] = (
    # ---- 美国 ----
    _Alias("DGS10", ("DGS10", "10年期美债", "10年期国债", "十年期国债", "10年期收益率"), (), _CN),
    _Alias("DGS2", ("DGS2", "2年期美债", "2年期国债", "两年期国债", "2年期收益率"), (), _CN),
    _Alias("DGS5", ("DGS5", "5年期美债", "5年期国债"), (), _CN),
    _Alias("DGS20", ("DGS20", "20年期美债", "20年期国债"), (), _CN),
    _Alias("T10Y2Y", ("T10Y2Y", "期限利差", "10年与2年", "美债利差")),
    _Alias("UNRATE", ("UNRATE", "失业率"), (), _CN),
    _Alias("PAYEMS", ("PAYEMS", "非农")),
    _Alias("ICSA", ("ICSA", "初请失业金")),
    _Alias(
        "CPIAUCSL",
        ("CPIAUCSL", "美国CPI", "美国通胀", "美国消费者物价", "美国居民消费价格"),
    ),
    _Alias("CPIAUCSL", ("CPI", "消费者物价"), (), (*_CN, "核心", "CORE_CPI", "CPI_CN")),
    _Alias("PCEPILFE", ("PCEPILFE", "核心PCE", "核心个人消费")),
    _Alias("FEDFUNDS", ("FEDFUNDS", "联邦基金利率", "联邦基金有效利率")),
    _Alias("GDP", ("美国GDP", "美国国内生产总值")),
    _Alias("GDP", ("GDP", "国内生产总值"), (), (*_CN, "实际", "GDP_CN", "GDPC1")),
    _Alias("GDPC1", ("GDPC1", "美国实际GDP")),
    _Alias("ISM_MFG", ("ISM_MFG", "ISM制造业", "ISM 制造业")),
    _Alias("ISM_MFG", ("PMI",), _US, ("ISM_SVC", "服务业")),
    _Alias("ISM_SVC", ("ISM_SVC", "ISM服务业", "ISM 服务业")),
    # ---- 中国 ----
    _Alias("CPI_CN", ("CPI_CN", "中国CPI", "国内CPI", "中国居民消费价格")),
    _Alias("CPI_CN", ("CPI", "居民消费价格"), _CN, ("核心", "CORE_CPI", *_US)),
    _Alias("CORE_CPI_CN", ("CORE_CPI_CN", "中国核心CPI")),
    _Alias("PPI_CN", ("PPI_CN", "PPI"), (), _US),
    _Alias("PMI_CN", ("PMI_CN", "中国PMI"), (), ("新订单", "新出口订单")),
    _Alias(
        "PMI_CN", ("PMI", "制造业采购经理"), _CN, (*_US, "ISM", "新订单", "新出口订单", "服务业")
    ),
    _Alias("M2_CN", ("M2_CN", "M2"), (), _US),
    _Alias("M1_CN", ("M1_CN", "M1"), (), _US),
    _Alias("INDUS_CN", ("INDUS_CN", "工业增加值")),
    _Alias("SOCIAL_FINANCING", ("SOCIAL_FINANCING", "社融", "社会融资规模"), (), ("存量",)),
    _Alias("RETAIL_CN", ("RETAIL_CN", "社会消费品零售", "社零")),
    _Alias("EXPORT_CN", ("EXPORT_CN", "中国出口")),
    _Alias("GDP_CN", ("GDP_CN", "中国GDP", "中国实际GDP")),
)

_VALUATION_NAMES = ("股债性价比", "股债收益差", "巴菲特指标", "巴菲特", "中位数市净率", "中位数PB")
_PCR_NAMES = ("PCR", "认沽认购", "认沽/认购", "看跌看涨比", "看跌/看涨")


def plan(question: str) -> list[PlannedCall]:
    """返回要预取的工具调用；不确定就返回空列表。"""

    text = question.upper()
    calls: list[PlannedCall] = []
    series = _series_codes(text)
    if 0 < len(series) <= MAX_PREFETCH:
        calls.extend(PlannedCall("macro_series", {"code": code}) for code in series)
    calls.extend(_indicator_calls(text))
    if not calls:
        calls.extend(_instrument_calls(question))
    return calls[:MAX_PREFETCH]


def _series_codes(text: str) -> list[str]:
    found: list[str] = []
    for alias in _ALIASES:
        if alias.code in found:
            continue
        if not any(name.upper() in text for name in alias.names):
            continue
        if alias.needs and not any(word.upper() in text for word in alias.needs):
            continue
        if any(word.upper() in text for word in alias.forbids):
            continue
        found.append(alias.code)
    for code in _tenor_codes(text):
        if code not in found:
            found.append(code)
    return found


_TENORS = {"2": "DGS2", "5": "DGS5", "10": "DGS10", "20": "DGS20"}
_CN_TENORS = {"两": "2", "二": "2", "五": "5", "十": "10", "二十": "20"}
_TENOR = re.compile(r"(二十|十|两|二|五|\d{1,2})年期")


def _tenor_codes(text: str) -> list[str]:
    """「2年期与10年期美债收益率」这类并列写法：别名表只覆盖连写，这里按期限补全。"""

    if not any(word in text for word in ("美债", "国债", "收益率")):
        return []
    if any(word.upper() in text for word in _CN):
        return []
    codes: list[str] = []
    for tenor in _TENOR.findall(text):
        code = _TENORS.get(_CN_TENORS.get(tenor, tenor))
        if code and code not in codes:
            codes.append(code)
    return codes


def _indicator_calls(text: str) -> list[PlannedCall]:
    calls: list[PlannedCall] = []
    if any(name.upper() in text for name in _VALUATION_NAMES):
        calls.append(PlannedCall("custom_indicator", {"indicator": "valuation"}))
    if any(name.upper() in text for name in _PCR_NAMES):
        groups = [group.code for group in OPTION_GROUPS if group.name.upper() in text]
        if len(groups) == 1:
            calls.append(PlannedCall("custom_indicator", {"indicator": "pcr", "group": groups[0]}))
    return calls


def _instrument_calls(question: str) -> list[PlannedCall]:
    codes = list(dict.fromkeys(_CODE.findall(question)))[:2]
    return [PlannedCall("instrument_search", {"query": code}) for code in codes]
