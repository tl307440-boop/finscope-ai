from datetime import date
from decimal import Decimal

from app.providers.errors import ProviderError
from app.providers.quality import RawBar

FIXTURE_NOTICE = "演示数据/非实时。这些价格是合成样例，不是真实行情。"


def fixture_bars(data_mode: str) -> list[RawBar]:
    if data_mode != "demo":
        raise ProviderError(
            code="DEMO_NOT_ALLOWED",
            message="当前不是演示模式，不会写入样例行情",
            status_code=409,
        )
    return [
        RawBar(
            trade_date=date(2024, 1, 2),
            open=Decimal("10.00"),
            high=Decimal("10.50"),
            low=Decimal("9.80"),
            close=Decimal("10.20"),
            volume=Decimal("1000"),
            amount=Decimal("10200"),
            adjustment="none",
            currency="CNY",
            unit="price",
            source_record_key="fixture:demo-bar",
        )
    ]
