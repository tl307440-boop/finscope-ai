from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

QualityFlag = Literal["ok", "review", "rejected"]
Adjustment = Literal["none", "qfq", "hfq"]
QUALITY_VERSION = "bar-quality-v1"


class RawBar(BaseModel):
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    # 不同来源提供的字段不同：东方财富给成交额，腾讯只给成交量。
    # 缺成交额时留空，界面渲染「—」，绝不用 量×价 之类的办法倒推。
    amount: Decimal | None = None
    adjustment: Adjustment = "none"
    currency: str = Field(min_length=3, max_length=8)
    unit: str = "price"
    source_record_key: str = Field(min_length=1, max_length=128)


class CheckedBar(BaseModel):
    trade_date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    amount: Decimal | None
    adjustment: Adjustment
    currency: str
    unit: str
    source_record_key: str
    quality_flag: Literal["ok", "review"]
    quality_version: str = QUALITY_VERSION


def classify_bar(bar: RawBar) -> QualityFlag:
    """OHLC 不合法或数量为负时拒绝入库。复权后价格偏离过大则进入待审。

    成交额可以缺（部分来源不提供），但一旦给了负数就照样拒绝。
    """
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(price <= 0 for price in prices) or bar.volume < 0:
        return "rejected"
    if bar.amount is not None and bar.amount < 0:
        return "rejected"
    if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
        return "rejected"
    if bar.high < bar.low:
        return "rejected"
    if bar.adjustment != "none" and (bar.high > bar.close * 5 or bar.low * 5 < bar.close):
        return "review"
    return "ok"


def accept_bar(bar: RawBar) -> CheckedBar | None:
    flag = classify_bar(bar)
    if flag == "rejected":
        return None
    return CheckedBar(
        trade_date=bar.trade_date,
        open=bar.open,
        high=bar.high,
        low=bar.low,
        close=bar.close,
        volume=bar.volume,
        amount=bar.amount,
        adjustment=bar.adjustment,
        currency=bar.currency,
        unit=bar.unit,
        source_record_key=bar.source_record_key,
        quality_flag=flag,
    )
