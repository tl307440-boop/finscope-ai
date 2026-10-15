from dataclasses import dataclass
from datetime import date

from app.utils.identifiers import canonical_code, display_symbol_for, normalize_cik


@dataclass(frozen=True)
class InstrumentSeed:
    market: str
    exchange: str
    symbol: str
    name_cn: str
    name_en: str
    instrument_type: str
    currency: str
    cik: str | None = None

    @property
    def display_symbol(self) -> str:
        return display_symbol_for(self.exchange, self.symbol)

    @property
    def canonical(self) -> str:
        return canonical_code(self.exchange, self.symbol)


@dataclass(frozen=True)
class CalendarSeed:
    exchange: str
    session_date: date
    is_open: bool
    source: str


_CALENDAR_SOURCE = "curated-sse-szse-2024-national-day"

_CN_INDEXES = (
    ("SSE", "000001", "上证指数", "SSE Composite"),
    ("SZSE", "399001", "深证成指", "SZSE Component"),
    ("SZSE", "399006", "创业板指", "ChiNext"),
    ("SSE", "000300", "沪深300", "CSI 300"),
)

_CN_STOCKS = (
    ("SSE", "600519", "贵州茅台", "Kweichow Moutai"),
    ("SSE", "601318", "中国平安", "Ping An Insurance"),
    ("SZSE", "000001", "平安银行", "Ping An Bank"),
    ("SZSE", "000858", "五粮液", "Wuliangye"),
    ("SZSE", "300750", "宁德时代", "CATL"),
    ("SSE", "600036", "招商银行", "China Merchants Bank"),
    ("SSE", "601899", "紫金矿业", "Zijin Mining"),
    ("SZSE", "002594", "比亚迪", "BYD"),
    ("SSE", "600900", "长江电力", "Yangtze Power"),
    ("SZSE", "000333", "美的集团", "Midea"),
    ("SSE", "601012", "隆基绿能", "LONGi"),
    ("SZSE", "300059", "东方财富", "East Money"),
    ("SSE", "600276", "恒瑞医药", "Hengrui"),
    ("SZSE", "002415", "海康威视", "Hikvision"),
    ("SSE", "601888", "中国中免", "CTG Duty Free"),
    ("SSE", "603259", "药明康德", "WuXi AppTec"),
    ("SZSE", "000568", "泸州老窖", "Luzhou Laojiao"),
    ("SSE", "600030", "中信证券", "CITIC Securities"),
    ("SZSE", "002475", "立讯精密", "Luxshare"),
    ("SSE", "601166", "兴业银行", "Industrial Bank"),
)

# CIK 来自 SEC 公开的公司编号，入库前补成 10 位。这里不请求 SEC。
_US_STOCKS = (
    ("NASDAQ", "AAPL", "苹果", "Apple", "320193"),
    ("NASDAQ", "MSFT", "微软", "Microsoft", "789019"),
    ("NASDAQ", "NVDA", "英伟达", "NVIDIA", "1045810"),
    ("NASDAQ", "AMZN", "亚马逊", "Amazon", "1018724"),
    ("NASDAQ", "GOOGL", "谷歌", "Alphabet Class A", "1652044"),
    ("NASDAQ", "META", "Meta", "Meta Platforms", "1326801"),
    ("NASDAQ", "TSLA", "特斯拉", "Tesla", "1318605"),
    ("NYSE", "JPM", "摩根大通", "JPMorgan Chase", "19617"),
)


def reference_instruments() -> tuple[InstrumentSeed, ...]:
    indexes = tuple(
        InstrumentSeed("CN", exchange, symbol, name_cn, name_en, "INDEX", "CNY")
        for exchange, symbol, name_cn, name_en in _CN_INDEXES
    )
    stocks = tuple(
        InstrumentSeed("CN", exchange, symbol, name_cn, name_en, "STOCK", "CNY")
        for exchange, symbol, name_cn, name_en in _CN_STOCKS
    )
    us_stocks = tuple(
        InstrumentSeed("US", exchange, symbol, name_cn, name_en, "STOCK", "USD", normalize_cik(cik))
        for exchange, symbol, name_cn, name_en, cik in _US_STOCKS
    )
    return indexes + stocks + us_stocks


def reference_calendar() -> tuple[CalendarSeed, ...]:
    """2024 年国庆窗口。休市日包含周二，调休开市包含周日，用来防止按星期猜测。"""
    closed = (
        date(2024, 10, 1),
        date(2024, 10, 2),
        date(2024, 10, 3),
        date(2024, 10, 4),
        date(2024, 10, 5),
        date(2024, 10, 6),
        date(2024, 10, 7),
        date(2024, 10, 13),
    )
    opened = (
        date(2024, 9, 29),
        date(2024, 9, 30),
        date(2024, 10, 8),
        date(2024, 10, 9),
        date(2024, 10, 10),
        date(2024, 10, 11),
        date(2024, 10, 12),
    )
    rows: list[CalendarSeed] = []
    for exchange in ("SSE", "SZSE"):
        rows.extend(CalendarSeed(exchange, day, False, _CALENDAR_SOURCE) for day in closed)
        rows.extend(CalendarSeed(exchange, day, True, _CALENDAR_SOURCE) for day in opened)
    return tuple(rows)
