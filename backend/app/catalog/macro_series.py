from dataclasses import dataclass


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


def fred_series_catalog() -> tuple[MacroSeriesSeed, ...]:
    """序列定义来自 FRED 系列页。这里没有观测值。"""

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
            source_agency="Board of Governors of the Federal Reserve System (US)",
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
            source_agency="Board of Governors of the Federal Reserve System (US)",
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
            source_agency="Board of Governors of the Federal Reserve System (US)",
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
    )


def catalog_codes() -> frozenset[str]:
    return frozenset(seed.series_code for seed in fred_series_catalog())
