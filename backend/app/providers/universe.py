"""全市场证券清单：美股来自 SEC 官方 ticker 表，A 股来自巨潮资讯的证券列表。

只取代码、名称、交易所这类公开元数据；不含任何行情或财务数字。
SEC 在文件说明里写明「不保证准确与完整」，所以这里只当作可搜索的目录，
财报是否存在要等真正抓取 companyfacts 后才知道（抓不到就如实标 unavailable）。
"""

import json
from dataclasses import dataclass
from typing import cast

from app.providers.errors import ProviderError
from app.providers.http_client import ResilientHttpClient
from app.utils.identifiers import normalize_cik

SEC_TICKERS_EXCHANGE = "https://www.sec.gov/files/company_tickers_exchange.json"
CNINFO_STOCK_LIST = "https://www.cninfo.com.cn/new/data/szse_stock.json"

# SEC 用的交易所名 → 本项目的交易所代码。OTC、CBOE 等不收：没有可靠的财报与日线覆盖。
_US_EXCHANGES = {"nasdaq": "NASDAQ", "nyse": "NYSE"}


@dataclass(frozen=True)
class UsListing:
    cik: str
    ticker: str
    name: str
    exchange: str


@dataclass(frozen=True)
class CnListing:
    code: str
    name: str
    exchange: str

    @property
    def display_symbol(self) -> str:
        return f"{self.code}.{'SH' if self.exchange == 'SSE' else 'SZ'}"


def parse_us_listings(payload: bytes) -> tuple[UsListing, ...]:
    """每个 CIK 只留一个代码（表里按市值排序，先出现的算主代码，如 GOOGL 先于 GOOG）。"""

    document = _object(payload)
    fields, rows = document.get("fields"), document.get("data")
    if not isinstance(fields, list) or not isinstance(rows, list):
        raise _payload_error("SEC ticker 表结构不对")
    index = {name: position for position, name in enumerate(fields) if isinstance(name, str)}
    if not {"cik", "name", "ticker", "exchange"} <= set(index):
        raise _payload_error("SEC ticker 表缺字段")
    seen: set[str] = set()
    found: list[UsListing] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < len(index):
            continue
        exchange = _US_EXCHANGES.get(str(row[index["exchange"]]).strip().lower())
        ticker = str(row[index["ticker"]]).strip().upper()
        name = str(row[index["name"]]).strip()
        raw_cik = row[index["cik"]]
        if exchange is None or not ticker or not name or not isinstance(raw_cik, int):
            continue
        if len(ticker) > 16:
            continue
        cik = normalize_cik(str(raw_cik))
        if cik in seen:
            continue
        seen.add(cik)
        found.append(UsListing(cik=cik, ticker=ticker, name=name[:128], exchange=exchange))
    return tuple(found)


def exchange_for_cn_code(code: str) -> str | None:
    """沪深 A 股：6 开头与 688 在上交所，0/3 开头在深交所。B 股、北交所不收。"""

    if len(code) != 6 or not code.isdigit():
        return None
    if code.startswith(("600", "601", "603", "605", "688")):
        return "SSE"
    if code.startswith(("000", "001", "002", "003", "300", "301")):
        return "SZSE"
    return None


def parse_cn_listings(payload: bytes) -> tuple[CnListing, ...]:
    document = _object(payload)
    rows = document.get("stockList")
    if not isinstance(rows, list):
        raise _payload_error("巨潮证券列表结构不对")
    found: dict[str, CnListing] = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        record = cast(dict[str, object], item)
        code, name = record.get("code"), record.get("zwjc")
        if not isinstance(code, str) or not isinstance(name, str) or not name.strip():
            continue
        exchange = exchange_for_cn_code(code)
        if exchange is None:
            continue
        found.setdefault(code, CnListing(code=code, name=name.strip()[:128], exchange=exchange))
    return tuple(found.values())


async def fetch_us_listings(client: ResilientHttpClient, user_agent: str) -> tuple[UsListing, ...]:
    if not user_agent.strip():
        raise ProviderError(
            code="DATA_UNAVAILABLE",
            message="未配置 SEC User-Agent，不能访问 EDGAR",
            status_code=503,
        )
    payload = await client.get(
        SEC_TICKERS_EXCHANGE,
        headers={"User-Agent": user_agent, "Accept": "application/json"},
    )
    return parse_us_listings(payload)


async def fetch_cn_listings(client: ResilientHttpClient) -> tuple[CnListing, ...]:
    return parse_cn_listings(await client.get(CNINFO_STOCK_LIST))


def _object(payload: bytes) -> dict[str, object]:
    try:
        decoded: object = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error("无法解析证券清单") from exc
    if not isinstance(decoded, dict):
        raise _payload_error("无法解析证券清单")
    return cast(dict[str, object], decoded)


def _payload_error(message: str) -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message=message, status_code=502)
