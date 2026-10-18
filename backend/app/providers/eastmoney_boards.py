"""东方财富板块（行业 / 概念）与热度榜的公开行情接口适配。

只做 URL 构造与响应解析，不发请求，便于离线测试。所有接口都是东财、同花顺网页
自身在浏览器里匿名调用的 JSON 接口，不带登录态、不破解签名；调用方负责限速与缓存。

口径（2026-10 实测）：
- 行业树来自 `quote.eastmoney.com/center/api/sidemenu_new.json` 的 `bklist`，
  type=2 为行业，flag=1/2/3 对应东财一/二/三级行业；type=3 为概念，type=1 为地域。
- 行情字段：f3 当日涨跌幅、f109 近 5 日涨跌幅、f160 近 10 日涨跌幅、f24 近 60 日涨跌幅、
  f25 年初至今涨跌幅，单位都是百分数；f62 当日主力净流入（元）、f164 近 5 日主力净流入（元）；
  f104/f105/f106 上涨/下跌/平盘家数。f160 经日 K 反算核对是 10 日而不是 20 日。
- 行业估值（PE_TTM 等）只覆盖东财二级行业，用 `ORIGINALCODE`（BK 代码的数字部分）关联。
- 行业研报按二级行业的数字代码归档，一级行业查不到。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal, cast
from urllib.parse import urlencode

from app.providers.errors import ProviderError

BoardKind = Literal["industry", "concept", "region"]

EASTMONEY_UT = "7eea3edcaed734bea9cbfc24409ed989"
BOARD_TREE_URL = "https://quote.eastmoney.com/center/api/sidemenu_new.json"
_ULIST_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_REPORT_URL = "https://reportapi.eastmoney.com/report/list"
EM_HOT_RANK_URL = "https://emappdata.eastmoney.com/stockrank/getAllCurrentList"
THS_HOT_STOCK_URL = (
    "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock"
    "?stock_type=a&type=hour&list_type=normal"
)
THS_HOT_PLATE_URL = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate?type={kind}"

EASTMONEY_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research; +https://quote.eastmoney.com/)",
    "Referer": "https://quote.eastmoney.com/",
}
THS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://eq.10jqka.com.cn/",
}

# 东财单页最多 100 条，再大直接 403。
PAGE_SIZE = 100
_KIND_BY_TYPE: dict[int, BoardKind] = {1: "region", 2: "industry", 3: "concept"}
_QUOTE_FIELDS = (
    "f2,f3,f6,f8,f9,f12,f14,f20,f24,f25,f62,f104,f105,f106,f109,f128,f136,f140,f160,f164"
)
_MEMBER_FIELDS = "f2,f3,f6,f8,f9,f12,f13,f14,f20,f23,f24,f25,f62,f100,f109,f160"
_HOT_QUOTE_FIELDS = "f2,f3,f12,f13,f14,f20,f100"


@dataclass(frozen=True)
class BoardNode:
    code: str
    name: str
    kind: BoardKind
    level: int


@dataclass(frozen=True)
class BoardQuote:
    code: str
    name: str
    last: Decimal | None
    change_pct: Decimal | None
    change_5d_pct: Decimal | None
    change_10d_pct: Decimal | None
    change_60d_pct: Decimal | None
    change_ytd_pct: Decimal | None
    amount: Decimal | None
    turnover_pct: Decimal | None
    pe_ttm: Decimal | None
    market_cap: Decimal | None
    main_net_inflow: Decimal | None
    main_net_inflow_5d: Decimal | None
    advancers: int | None
    decliners: int | None
    unchanged: int | None
    leader_code: str | None
    leader_name: str | None
    leader_change_pct: Decimal | None


@dataclass(frozen=True)
class MemberQuote:
    code: str
    exchange: str | None
    name: str
    last: Decimal | None
    change_pct: Decimal | None
    change_5d_pct: Decimal | None
    change_10d_pct: Decimal | None
    change_60d_pct: Decimal | None
    change_ytd_pct: Decimal | None
    amount: Decimal | None
    turnover_pct: Decimal | None
    pe_dynamic: Decimal | None
    pb: Decimal | None
    market_cap: Decimal | None
    main_net_inflow: Decimal | None
    sub_industry: str | None


@dataclass(frozen=True)
class MemberPage:
    total: int
    rows: tuple[MemberQuote, ...]


@dataclass(frozen=True)
class BoardBar:
    trade_date: date
    open: Decimal
    close: Decimal
    high: Decimal
    low: Decimal
    volume: Decimal
    amount: Decimal


@dataclass(frozen=True)
class ValuationPoint:
    trade_date: date
    pe_ttm: Decimal | None
    pb_mrq: Decimal | None
    ps_ttm: Decimal | None
    members: int | None
    loss_members: int | None


@dataclass(frozen=True)
class ValuationPage:
    pages: int
    rows: tuple[ValuationPoint, ...]


@dataclass(frozen=True)
class IndustryReport:
    info_code: str
    title: str
    org_name: str
    published_on: date
    rating: str | None
    researcher: str | None
    url: str


@dataclass(frozen=True)
class HotRankEntry:
    exchange: str
    code: str
    rank: int
    rank_change: int | None


@dataclass(frozen=True)
class StockQuote:
    code: str
    name: str
    last: Decimal | None
    change_pct: Decimal | None
    industry: str | None


@dataclass(frozen=True)
class ThsHotStock:
    code: str
    name: str
    rank: int
    heat: Decimal | None
    change_pct: Decimal | None
    rank_change: int | None
    concept_tags: tuple[str, ...]
    popularity_tag: str | None
    analysis: str | None


@dataclass(frozen=True)
class ThsHotPlate:
    code: str
    name: str
    rank: int
    heat: Decimal | None
    change_pct: Decimal | None
    rank_change: int | None
    hot_tag: str | None
    tag: str | None


def board_secid(code: str) -> str:
    return f"90.{normalize_board_code(code)}"


def normalize_board_code(code: str) -> str:
    text = code.strip().upper()
    if len(text) != 6 or not text.startswith("BK") or not text[2:].isdigit():
        raise ProviderError(code="VALIDATION_ERROR", message="板块代码形如 BK1036", status_code=422)
    return text


def board_number(code: str) -> str:
    """BK1036 → 1036，BK0475 → 475：估值库与研报库都用去掉前导零的数字代码。"""

    return str(int(normalize_board_code(code)[2:]))


def quote_url(codes: list[str] | tuple[str, ...]) -> str:
    secids = ",".join(board_secid(code) for code in codes)
    query = urlencode(
        {"fltt": "2", "invt": "2", "ut": EASTMONEY_UT, "fields": _QUOTE_FIELDS, "secids": secids}
    )
    return f"{_ULIST_URL}?{query}"


def stock_quote_url(secids: list[str] | tuple[str, ...]) -> str:
    query = urlencode(
        {
            "fltt": "2",
            "invt": "2",
            "ut": EASTMONEY_UT,
            "fields": _HOT_QUOTE_FIELDS,
            "secids": ",".join(secids),
        }
    )
    return f"{_ULIST_URL}?{query}"


def watch_quote_url(secids: list[str] | tuple[str, ...]) -> str:
    """一组个股的行情（字段与板块成分相同），一次最多 PAGE_SIZE 只。"""

    query = urlencode(
        {
            "fltt": "2",
            "invt": "2",
            "ut": EASTMONEY_UT,
            "fields": _MEMBER_FIELDS,
            "secids": ",".join(secids),
        }
    )
    return f"{_ULIST_URL}?{query}"


def parse_watch_quotes(payload: bytes) -> dict[str, MemberQuote]:
    quotes: dict[str, MemberQuote] = {}
    for row in _diff_rows(payload, "个股行情"):
        quote = _member(row)
        if quote is not None:
            quotes[quote.code] = quote
    return quotes


def parse_secid_quotes(payload: bytes) -> dict[str, MemberQuote]:
    """按东财 secid（市场.代码）索引的行情。

    板块、指数、美股、债券收益率与 A 股共用同一个接口，但代码会撞（A 股 000660 与韩股
    000660），所以不能像 parse_watch_quotes 那样只用代码做键。
    """

    quotes: dict[str, MemberQuote] = {}
    for row in _diff_rows(payload, "标的行情"):
        quote = _member(row)
        market = _int(row.get("f13"))
        if quote is not None and market is not None:
            quotes[f"{market}.{quote.code}"] = quote
    return quotes


def members_url(code: str, page: int) -> str:
    query = urlencode(
        {
            "pn": str(page),
            "pz": str(PAGE_SIZE),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "ut": EASTMONEY_UT,
            "fid": "f20",
            "fs": f"b:{normalize_board_code(code)} f:!50",
            "fields": _MEMBER_FIELDS,
        }
    )
    return f"{_CLIST_URL}?{query}"


def kline_url(secid: str, start: date, end: date) -> str:
    query = urlencode(
        {
            "secid": secid,
            "ut": EASTMONEY_UT,
            "fields1": "f1,f2,f3",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": "101",
            "fqt": "0",
            "beg": start.strftime("%Y%m%d"),
            "end": end.strftime("%Y%m%d"),
        }
    )
    return f"{_KLINE_URL}?{query}"


def valuation_url(code: str, page: int, page_size: int = 500) -> str:
    query = urlencode(
        {
            "reportName": "RPT_VALUEINDUSTRY_DET",
            "columns": "BOARD_NAME,ORIGINALCODE,TRADE_DATE,PE_TTM,PB_MRQ,PS_TTM,NUM,LOSS_COUNT",
            "pageNumber": str(page),
            "pageSize": str(page_size),
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
            "filter": f'(ORIGINALCODE="{board_number(code)}")',
        }
    )
    return f"{_DATACENTER_URL}?{query}"


def reports_url(code: str, start: date, end: date, page_size: int = 30) -> str:
    query = urlencode(
        {
            "industryCode": board_number(code),
            "pageSize": str(page_size),
            "industry": "*",
            "rating": "*",
            "ratingChange": "*",
            "beginTime": start.isoformat(),
            "endTime": end.isoformat(),
            "pageNo": "1",
            "fields": "",
            "qType": "1",
            "orgCode": "",
            "rcode": "",
        }
    )
    return f"{_REPORT_URL}?{query}"


def hot_rank_body(page_size: int = 50) -> bytes:
    return json.dumps(
        {
            "appId": "appId01",
            "globalId": "786e4c21-70dc-435a-93bb-38",
            "marketType": "",
            "pageNo": 1,
            "pageSize": page_size,
        }
    ).encode()


def parse_board_tree(payload: bytes) -> tuple[BoardNode, ...]:
    document = _object(_decode(payload, "板块目录"), "板块目录")
    nodes: list[BoardNode] = []
    seen: set[str] = set()
    for raw in _list(document.get("bklist"), "板块目录"):
        row = _object(raw, "板块目录")
        code = row.get("code")
        name = row.get("name")
        kind = _KIND_BY_TYPE.get(_int(row.get("type")) or 0)
        if not isinstance(code, str) or not isinstance(name, str) or kind is None:
            continue
        if code in seen:
            continue
        seen.add(code)
        level = (_int(row.get("flag")) or 0) if kind == "industry" else 0
        nodes.append(BoardNode(code=code, name=name.strip(), kind=kind, level=level))
    if not nodes:
        raise _payload_error("板块目录")
    return tuple(nodes)


def parse_board_quotes(payload: bytes) -> tuple[BoardQuote, ...]:
    rows = _diff_rows(payload, "板块行情")
    quotes: list[BoardQuote] = []
    for row in rows:
        code = _text(row.get("f12"))
        name = _text(row.get("f14"))
        if code is None or name is None:
            continue
        quotes.append(
            BoardQuote(
                code=code,
                name=name,
                last=_number(row.get("f2")),
                change_pct=_number(row.get("f3")),
                change_5d_pct=_number(row.get("f109")),
                change_10d_pct=_number(row.get("f160")),
                change_60d_pct=_number(row.get("f24")),
                change_ytd_pct=_number(row.get("f25")),
                amount=_number(row.get("f6")),
                turnover_pct=_number(row.get("f8")),
                pe_ttm=_number(row.get("f9")),
                market_cap=_number(row.get("f20")),
                main_net_inflow=_number(row.get("f62")),
                main_net_inflow_5d=_number(row.get("f164")),
                advancers=_int(row.get("f104")),
                decliners=_int(row.get("f105")),
                unchanged=_int(row.get("f106")),
                leader_code=_text(row.get("f140")),
                leader_name=_text(row.get("f128")),
                leader_change_pct=_number(row.get("f136")),
            )
        )
    return tuple(quotes)


def parse_members(payload: bytes) -> MemberPage:
    document = _object(_decode(payload, "板块成分"), "板块成分")
    data = document.get("data")
    if data is None:
        return MemberPage(total=0, rows=())
    body = _object(data, "板块成分")
    rows: list[MemberQuote] = []
    for raw in _list(body.get("diff") or [], "板块成分"):
        member = _member(_object(raw, "板块成分"))
        if member is not None:
            rows.append(member)
    return MemberPage(total=_int(body.get("total")) or len(rows), rows=tuple(rows))


def _member(row: dict[str, object]) -> MemberQuote | None:
    code = _text(row.get("f12"))
    name = _text(row.get("f14"))
    if code is None or name is None:
        return None
    return MemberQuote(
        code=code,
        exchange=_exchange(row.get("f13")),
        name=name,
        last=_number(row.get("f2")),
        change_pct=_number(row.get("f3")),
        change_5d_pct=_number(row.get("f109")),
        change_10d_pct=_number(row.get("f160")),
        change_60d_pct=_number(row.get("f24")),
        change_ytd_pct=_number(row.get("f25")),
        amount=_number(row.get("f6")),
        turnover_pct=_number(row.get("f8")),
        pe_dynamic=_number(row.get("f9")),
        pb=_number(row.get("f23")),
        market_cap=_number(row.get("f20")),
        main_net_inflow=_number(row.get("f62")),
        sub_industry=_text(row.get("f100")),
    )


def parse_klines(payload: bytes) -> tuple[BoardBar, ...]:
    document = _object(_decode(payload, "板块日线"), "板块日线")
    data = document.get("data")
    if data is None:
        return ()
    lines = _object(data, "板块日线").get("klines")
    if lines is None:
        return ()
    bars: list[BoardBar] = []
    seen: set[date] = set()
    for raw in _list(lines, "板块日线"):
        if not isinstance(raw, str):
            raise _payload_error("板块日线")
        parts = raw.split(",")
        if len(parts) < 7:
            raise _payload_error("板块日线")
        try:
            bar = BoardBar(
                trade_date=date.fromisoformat(parts[0]),
                open=Decimal(parts[1]),
                close=Decimal(parts[2]),
                high=Decimal(parts[3]),
                low=Decimal(parts[4]),
                volume=Decimal(parts[5]),
                amount=Decimal(parts[6]),
            )
        except (ValueError, InvalidOperation) as exc:
            raise _payload_error("板块日线") from exc
        if bar.trade_date in seen:
            continue
        seen.add(bar.trade_date)
        bars.append(bar)
    return tuple(sorted(bars, key=lambda item: item.trade_date))


def parse_valuation(payload: bytes) -> ValuationPage:
    document = _object(_decode(payload, "行业估值"), "行业估值")
    result = document.get("result")
    if result is None:
        return ValuationPage(pages=0, rows=())
    body = _object(result, "行业估值")
    points: list[ValuationPoint] = []
    for raw in _list(body.get("data") or [], "行业估值"):
        row = _object(raw, "行业估值")
        day = _day(row.get("TRADE_DATE"))
        if day is None:
            continue
        points.append(
            ValuationPoint(
                trade_date=day,
                pe_ttm=_number(row.get("PE_TTM")),
                pb_mrq=_number(row.get("PB_MRQ")),
                ps_ttm=_number(row.get("PS_TTM")),
                members=_int(row.get("NUM")),
                loss_members=_int(row.get("LOSS_COUNT")),
            )
        )
    return ValuationPage(pages=_int(body.get("pages")) or 0, rows=tuple(points))


def parse_reports(payload: bytes) -> tuple[IndustryReport, ...]:
    document = _object(_decode(payload, "行业研报"), "行业研报")
    reports: list[IndustryReport] = []
    for raw in _list(document.get("data") or [], "行业研报"):
        row = _object(raw, "行业研报")
        info_code = _text(row.get("infoCode"))
        title = _text(row.get("title"))
        day = _day(row.get("publishDate"))
        if info_code is None or title is None or day is None:
            continue
        reports.append(
            IndustryReport(
                info_code=info_code,
                title=title,
                org_name=_text(row.get("orgSName")) or _text(row.get("orgName")) or "—",
                published_on=day,
                rating=_text(row.get("emRatingName")) or _text(row.get("sRatingName")),
                researcher=_text(row.get("researcher")),
                url=f"https://data.eastmoney.com/report/zw_industry.jshtml?infocode={info_code}",
            )
        )
    return tuple(reports)


def parse_hot_rank(payload: bytes) -> tuple[HotRankEntry, ...]:
    document = _object(_decode(payload, "人气榜"), "人气榜")
    entries: list[HotRankEntry] = []
    for raw in _list(document.get("data") or [], "人气榜"):
        row = _object(raw, "人气榜")
        symbol = _text(row.get("sc"))
        rank = _int(row.get("rk"))
        if symbol is None or rank is None or len(symbol) != 8:
            continue
        exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(symbol[:2])
        if exchange is None:
            continue
        entries.append(
            HotRankEntry(
                exchange=exchange, code=symbol[2:], rank=rank, rank_change=_int(row.get("rc"))
            )
        )
    return tuple(sorted(entries, key=lambda item: item.rank))


def stock_secid(exchange: str, code: str) -> str:
    return f"{'1' if exchange == 'SSE' else '0'}.{code}"


def parse_stock_quotes(payload: bytes) -> dict[str, StockQuote]:
    quotes: dict[str, StockQuote] = {}
    for row in _diff_rows(payload, "个股行情"):
        code = _text(row.get("f12"))
        name = _text(row.get("f14"))
        if code is None or name is None:
            continue
        quotes[code] = StockQuote(
            code=code,
            name=name,
            last=_number(row.get("f2")),
            change_pct=_number(row.get("f3")),
            industry=_text(row.get("f100")),
        )
    return quotes


def parse_ths_hot_stocks(payload: bytes) -> tuple[ThsHotStock, ...]:
    data = _ths_data(payload, "同花顺热股")
    stocks: list[ThsHotStock] = []
    for raw in _list(data.get("stock_list") or [], "同花顺热股"):
        row = _object(raw, "同花顺热股")
        code = _text(row.get("code"))
        name = _text(row.get("name"))
        rank = _int(row.get("order"))
        if code is None or name is None or rank is None:
            continue
        tag = row.get("tag")
        tags = cast(dict[str, object], tag) if isinstance(tag, dict) else {}
        concepts = tags.get("concept_tag")
        stocks.append(
            ThsHotStock(
                code=code,
                name=name,
                rank=rank,
                heat=_number(row.get("rate")),
                change_pct=_number(row.get("rise_and_fall")),
                rank_change=_int(row.get("hot_rank_chg")),
                concept_tags=tuple(
                    item
                    for item in (concepts if isinstance(concepts, list) else [])
                    if isinstance(item, str)
                ),
                popularity_tag=_text(tags.get("popularity_tag")),
                analysis=_text(row.get("analyse")),
            )
        )
    return tuple(sorted(stocks, key=lambda item: item.rank))


def parse_ths_hot_plates(payload: bytes) -> tuple[ThsHotPlate, ...]:
    data = _ths_data(payload, "同花顺热门板块")
    plates: list[ThsHotPlate] = []
    for raw in _list(data.get("plate_list") or [], "同花顺热门板块"):
        row = _object(raw, "同花顺热门板块")
        code = _text(row.get("code"))
        name = _text(row.get("name"))
        rank = _int(row.get("order"))
        if code is None or name is None or rank is None:
            continue
        plates.append(
            ThsHotPlate(
                code=code,
                name=name,
                rank=rank,
                heat=_number(row.get("rate")),
                change_pct=_number(row.get("rise_and_fall")),
                rank_change=_int(row.get("hot_rank_chg")),
                hot_tag=_text(row.get("hot_tag")),
                tag=_text(row.get("tag")),
            )
        )
    return tuple(sorted(plates, key=lambda item: item.rank))


def _ths_data(payload: bytes, label: str) -> dict[str, object]:
    document = _object(_decode(payload, label), label)
    if _int(document.get("status_code")) not in (0, None):
        raise ProviderError(code="UPSTREAM_ERROR", message=f"{label}接口返回错误", status_code=502)
    return _object(document.get("data") or {}, label)


def _diff_rows(payload: bytes, label: str) -> list[dict[str, object]]:
    document = _object(_decode(payload, label), label)
    data = document.get("data")
    if data is None:
        return []
    diff = _object(data, label).get("diff")
    if diff is None:
        return []
    if isinstance(diff, dict):
        diff = list(cast(dict[str, object], diff).values())
    return [_object(item, label) for item in _list(diff, label)]


def _decode(payload: bytes, label: str) -> object:
    try:
        return cast(object, json.loads(payload, parse_float=Decimal))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _payload_error(label) from exc


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _payload_error(label)
    return cast(dict[str, object], value)


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise _payload_error(label)
    return cast(list[object], value)


def _number(value: object) -> Decimal | None:
    """东财用 "-" 表示停牌或无数据；保留 None，不补 0。"""

    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        text = value.strip()
        if not text or text == "-":
            return None
        try:
            parsed = Decimal(text)
        except InvalidOperation:
            return None
        return parsed if parsed.is_finite() else None
    return None


def _int(value: object) -> int | None:
    number = _number(value)
    if number is None or number != number.to_integral_value():
        return None
    return int(number)


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip() != "-":
        return value.strip()
    return None


def _day(value: object) -> date | None:
    text = _text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace(" 00:00:00.000", " 00:00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _exchange(value: object) -> str | None:
    market = _int(value)
    return {1: "SSE", 0: "SZSE"}.get(market) if market is not None else None


def _payload_error(label: str) -> ProviderError:
    return ProviderError(code="UPSTREAM_PAYLOAD", message=f"无法解析{label}响应", status_code=502)
