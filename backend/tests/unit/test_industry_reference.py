from app.catalog.industry_reference import SW_LEVEL1
from app.catalog.reference_data import reference_instruments
from app.services.market_temperature import MIN_SECTOR_NAMES


def test_every_member_is_a_reference_cn_stock_and_appears_once() -> None:
    stocks = {
        (seed.exchange, seed.symbol)
        for seed in reference_instruments()
        if seed.market == "CN" and seed.instrument_type == "STOCK"
    }
    members = [member for seed in SW_LEVEL1 for member in seed.members]
    assert len(members) == len(set(members))
    assert set(members) <= stocks
    assert set(members) == stocks


def test_codes_are_unique_and_some_sectors_are_computable() -> None:
    codes = [seed.code for seed in SW_LEVEL1]
    assert len(codes) == len(set(codes))
    assert sum(1 for seed in SW_LEVEL1 if len(seed.members) >= MIN_SECTOR_NAMES) >= 3
