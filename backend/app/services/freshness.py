"""数据新鲜度判定（纯函数，可离线测试）。

沿用 dbt source freshness / Dagster freshness policy 的两级阈值：
超过 ``warn_after`` 报「偏旧」，超过 ``error_after`` 报「已过期」；没有任何数据是「缺失」。
阈值按数据本身的节奏定，不是一刀切：日线隔周末最多 3 天，月度宏观要容忍发布滞后。
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal

Freshness = Literal["fresh", "warn", "stale", "missing"]


@dataclass(frozen=True)
class Threshold:
    warn_after: timedelta
    error_after: timedelta


# 日线：周五收盘到周一早晨相隔 3 天；长假最多约 9 天，所以 10 天才算过期。
BARS = Threshold(timedelta(days=4), timedelta(days=10))
# 财报同步：按「上次成功检查」的时间算。申报是低频事件，但调度器每天至少检查一次。
FINANCIALS = Threshold(timedelta(hours=48), timedelta(days=7))
# 证券清单每周同步一次。
UNIVERSE = Threshold(timedelta(days=9), timedelta(days=21))
# 宏观按发布节奏：观测日期是期初，月度数据要 2~6 周后才发布，季度数据更晚。
MACRO: dict[str, Threshold] = {
    "daily": Threshold(timedelta(days=5), timedelta(days=10)),
    "weekly": Threshold(timedelta(days=12), timedelta(days=24)),
    "monthly": Threshold(timedelta(days=60), timedelta(days=100)),
    "quarterly": Threshold(timedelta(days=200), timedelta(days=270)),
}
_MACRO_FALLBACK = MACRO["monthly"]

LABELS: dict[Freshness, str] = {
    "fresh": "最新",
    "warn": "偏旧",
    "stale": "已过期",
    "missing": "暂无数据",
}


def classify(age: timedelta | None, threshold: Threshold) -> Freshness:
    if age is None:
        return "missing"
    if age > threshold.error_after:
        return "stale"
    if age > threshold.warn_after:
        return "warn"
    return "fresh"


def macro_threshold(frequency: str) -> Threshold:
    return MACRO.get(frequency.strip().lower(), _MACRO_FALLBACK)


def day_state(latest: date | None, today: date, threshold: Threshold) -> Freshness:
    """按「数据日期」距今多久判定（日线、宏观）。未来日期按 0 天算，不把它当过期。"""

    if latest is None:
        return "missing"
    return classify(max(today - latest, timedelta(0)), threshold)


def sync_state(last_success: datetime | None, now: datetime, threshold: Threshold) -> Freshness:
    """按「上次成功同步」距今多久判定（财报、清单）。"""

    if last_success is None:
        return "missing"
    return classify(max(now - last_success, timedelta(0)), threshold)


def worst(states: list[Freshness]) -> Freshness:
    order: tuple[Freshness, ...] = ("fresh", "warn", "stale", "missing")
    if not states:
        return "missing"
    return max(states, key=order.index)
