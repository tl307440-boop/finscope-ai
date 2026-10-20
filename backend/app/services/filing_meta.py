"""从公告标题判断报告类型和期次。纯函数，判断不了就归到 other、期次留空，不猜。"""

import re
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FilingHit:
    code: str
    title: str
    url: str
    published_at: datetime
    report_type: str
    period: str | None


_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("forecast", ("业绩预告", "业绩预盈", "业绩预亏", "预增", "预减")),
    ("express", ("业绩快报",)),
    ("inquiry", ("问询函", "关注函", "监管函")),
    ("annual", ("年度报告", "年报")),
    ("semiannual", ("半年度报告", "半年报")),
    ("q1", ("一季度报告", "第一季度报告")),
    ("q3", ("三季度报告", "第三季度报告")),
)
_YEAR = re.compile(r"(20\d{2})\s*年")


def report_type_of(title: str) -> str:
    for name, words in _TYPES:
        if any(word in title for word in words):
            return name
    return "other"


def period_of(title: str) -> str | None:
    found = _YEAR.search(title)
    if found is None:
        return None
    year = found.group(1)
    if any(word in title for word in ("前三季度", "三季度", "第三季度")):
        return f"{year}Q3"
    if any(word in title for word in ("一季度", "第一季度")):
        return f"{year}Q1"
    if any(word in title for word in ("半年度", "上半年")):
        return f"{year}H1"
    if "年度" in title or "年报" in title:
        return year
    return None
