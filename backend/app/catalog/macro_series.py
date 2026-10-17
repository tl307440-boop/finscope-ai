from dataclasses import dataclass
from typing import Literal

MacroProvider = Literal["fred", "eastmoney"]


@dataclass(frozen=True)
class MacroSeriesSeed:
    series_code: str
    name_cn: str
    name_en: str
    category: str
    frequency: str
    base_unit: str
    seasonal_adjustment: str
    source_agency: str
    provider: MacroProvider = "fred"
    # 东方财富指标 ID；FRED 序列留空（序列代码即 FRED ID）。
    source_key: str = ""


_FED_BOARD = "Board of Governors of the Federal Reserve System (US)"
_ISM_VIA_EASTMONEY = "Institute for Supply Management (via Eastmoney)"


def fred_series_catalog() -> tuple[MacroSeriesSeed, ...]:
    """序列定义来自 FRED 系列页与东方财富数据中心报表。这里没有观测值。"""

    return (
        MacroSeriesSeed(
            series_code="GDP",
            name_cn="美国国内生产总值",
            name_en="Gross Domestic Product",
            category="output",
            frequency="Quarterly",
            base_unit="Billions of Dollars",
            seasonal_adjustment="Seasonally Adjusted Annual Rate",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="UNRATE",
            name_cn="美国失业率",
            name_en="Unemployment Rate",
            category="labor",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="PAYEMS",
            name_cn="非农就业人数",
            name_en="All Employees, Total Nonfarm",
            category="labor",
            frequency="Monthly",
            base_unit="Thousands of Persons",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="CPIAUCSL",
            name_cn="美国消费者物价指数",
            name_en=(
                "Consumer Price Index for All Urban Consumers: All Items in U.S. City Average"
            ),
            category="inflation",
            frequency="Monthly",
            base_unit="Index 1982-1984=100",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="PCEPILFE",
            name_cn="核心个人消费支出价格指数",
            name_en=(
                "Personal Consumption Expenditures Excluding Food and Energy "
                "(Chain-Type Price Index)"
            ),
            category="inflation",
            frequency="Monthly",
            base_unit="Index 2017=100",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="FEDFUNDS",
            name_cn="联邦基金有效利率",
            name_en="Federal Funds Effective Rate",
            category="rates",
            frequency="Monthly",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DGS2",
            name_cn="美国2年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 2-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DGS10",
            name_cn="美国10年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="T10Y2Y",
            name_cn="10年与2年期国债利差",
            name_en="10-Year Treasury Constant Maturity Minus 2-Year Treasury Constant Maturity",
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency="Federal Reserve Bank of St. Louis",
        ),
        MacroSeriesSeed(
            series_code="CES0500000003",
            name_cn="平均时薪（私营非农）",
            name_en="Average Hourly Earnings of All Employees, Total Private",
            category="labor",
            frequency="Monthly",
            base_unit="Dollars per Hour",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Bureau of Labor Statistics",
        ),
        MacroSeriesSeed(
            series_code="GDPC1",
            name_cn="实际国内生产总值",
            name_en="Real Gross Domestic Product",
            category="output",
            frequency="Quarterly",
            base_unit="Billions of Chained 2017 Dollars",
            seasonal_adjustment="Seasonally Adjusted Annual Rate",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="PCEC96",
            name_cn="实际个人消费支出",
            name_en="Real Personal Consumption Expenditures",
            category="consumption",
            frequency="Monthly",
            base_unit="Billions of Chained 2017 Dollars",
            seasonal_adjustment="Seasonally Adjusted Annual Rate",
            source_agency="U.S. Bureau of Economic Analysis",
        ),
        MacroSeriesSeed(
            series_code="DGS5",
            name_cn="美国5年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 5-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DGS20",
            name_cn="美国20年期国债收益率",
            name_en=(
                "Market Yield on U.S. Treasury Securities at 20-Year Constant Maturity, "
                "Quoted on an Investment Basis"
            ),
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DFEDTARL",
            name_cn="联邦基金目标利率下限",
            name_en="Federal Funds Target Range - Lower Limit",
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="DFEDTARU",
            name_cn="联邦基金目标利率上限",
            name_en="Federal Funds Target Range - Upper Limit",
            category="rates",
            frequency="Daily",
            base_unit="Percent",
            seasonal_adjustment="Not Seasonally Adjusted",
            source_agency=_FED_BOARD,
        ),
        MacroSeriesSeed(
            series_code="ICSA",
            name_cn="初请失业金人数",
            name_en="Initial Claims",
            category="labor",
            frequency="Weekly",
            base_unit="Number",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency="U.S. Employment and Training Administration",
        ),
        MacroSeriesSeed(
            series_code="ISM_MFG",
            name_cn="ISM 制造业 PMI",
            name_en="ISM Manufacturing PMI",
            category="sentiment",
            frequency="Monthly",
            base_unit="Index",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency=_ISM_VIA_EASTMONEY,
            provider="eastmoney",
            source_key="EMG00002790",
        ),
        MacroSeriesSeed(
            series_code="ISM_SVC",
            name_cn="ISM 服务业 PMI",
            name_en="ISM Services PMI",
            category="sentiment",
            frequency="Monthly",
            base_unit="Index",
            seasonal_adjustment="Seasonally Adjusted",
            source_agency=_ISM_VIA_EASTMONEY,
            provider="eastmoney",
            source_key="EMG00002791",
        ),
    )


def catalog_codes() -> frozenset[str]:
    return frozenset(seed.series_code for seed in fred_series_catalog())


def fred_codes() -> frozenset[str]:
    """只发往 FRED 的序列；东方财富序列走自己的 provider。"""

    return frozenset(seed.series_code for seed in fred_series_catalog() if seed.provider == "fred")


def seed_for(series_code: str) -> MacroSeriesSeed | None:
    for seed in fred_series_catalog():
        if seed.series_code == series_code:
            return seed
    return None
