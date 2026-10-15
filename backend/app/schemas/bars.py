from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.repositories.macro import decimal_text
from app.repositories.market_bars import StoredDailyBar
from app.services.bar_indicators import moving_average, simple_return

EMPTY_BARS = "暂无已同步的日线。未同步的日期不会用演示价格填补。"


class DailyBarItem(BaseModel):
    trade_date: date
    open: str
    high: str
    low: str
    close: str
    volume: str
    amount: str
    currency: Literal["CNY"]
    volume_unit: Literal["hand"]
    adjustment: Literal["none", "qfq", "hfq"]
    quality_flag: str
    ma5: str | None = None
    ma10: str | None = None
    ma20: str | None = None
    ma60: str | None = None
    simple_return: str | None = None


class DailyBarPage(BaseModel):
    instrument_id: str
    display_symbol: str
    name_cn: str
    timeframe: Literal["1d"]
    adjustment: Literal["none", "qfq", "hfq"]
    source: Literal["akshare"]
    delay: Literal["eod"]
    pit_date: date
    bars: list[DailyBarItem]
    empty_message: str | None = None
    truncated: bool


class AkshareSyncRequest(BaseModel):
    display_symbol: str = Field(pattern=r"^[0-9]{6}\.(SH|SZ)$")
    observation_start: date
    observation_end: date
    adjustment: Literal["none", "qfq", "hfq"] = "none"


class AkshareSyncResult(BaseModel):
    display_symbol: str
    rows_written: int
    rows_rejected: int
    duplicates_skipped: int
    preserved_existing: bool


def build_bar_page(
    *,
    instrument_id: str,
    display_symbol: str,
    name_cn: str,
    adjustment: Literal["none", "qfq", "hfq"],
    pit: date,
    rows: list[StoredDailyBar],
    truncated: bool,
) -> DailyBarPage:
    return DailyBarPage(
        instrument_id=instrument_id,
        display_symbol=display_symbol,
        name_cn=name_cn,
        timeframe="1d",
        adjustment=adjustment,
        source="akshare",
        delay="eod",
        pit_date=pit,
        bars=[_item(row, rows, index, adjustment) for index, row in enumerate(rows)],
        empty_message=EMPTY_BARS if not rows else None,
        truncated=truncated,
    )


def _item(
    row: StoredDailyBar,
    rows: list[StoredDailyBar],
    index: int,
    adjustment: Literal["none", "qfq", "hfq"],
) -> DailyBarItem:
    closes = [item.close for item in rows[: index + 1]]
    previous = None if index == 0 else rows[index - 1].close
    return DailyBarItem(
        trade_date=row.trade_date,
        open=decimal_text(row.open),
        high=decimal_text(row.high),
        low=decimal_text(row.low),
        close=decimal_text(row.close),
        volume=decimal_text(row.volume),
        amount=decimal_text(row.amount),
        currency="CNY",
        volume_unit="hand",
        adjustment=adjustment,
        quality_flag=row.quality_flag,
        ma5=_text(moving_average(closes, 5)),
        ma10=_text(moving_average(closes, 10)),
        ma20=_text(moving_average(closes, 20)),
        ma60=_text(moving_average(closes, 60)),
        simple_return=_text(simple_return(previous, row.close)),
    )


def _text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return decimal_text(value)
