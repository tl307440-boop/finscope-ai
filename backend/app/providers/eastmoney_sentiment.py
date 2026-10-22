"""东方财富公开的市场情绪数据：涨停 / 炸板 / 跌停股池与机构调研。

只做 URL 构造与解析，不发请求。股池接口是东财「涨停板行情」页面匿名调用的 JSON；
机构调研来自数据中心 `RPT_ORG_SURVEYNEW`（上市公司公告披露的接待记录）。

口径（2026-10 实测）：
- 股池价格字段 p 为「元 × 1000」的整数；zdp 为涨跌幅（%）；fbt / lbt 为首次 / 最后封板时间 HHMMSS；
  fund 为封板资金（元）；lbc 为连板数；zbc 为炸板次数；hybk 为所属行业。
- 非交易日查询返回 data = null。
- 涨跌分布 `getTopicZDFenBu`：fenbu 为 `[{"档位": 家数}, ...]`；档位 11 / -11 为涨停 / 跌停
  （与股池家数一致），0 为平盘，其余为 1 个百分点宽的涨跌幅档（1 = (0%, 1%]，-1 = [-1%, 0%)）。
  覆盖沪深京全部 A 股，qdate 为交易日（YYYYMMDD）。
- 机构调研每条是一次接待活动（NUMBERNEW=1 只取首条），SUM 为参与机构数，NOTICE_DATE 为公告日。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from urllib.parse import urlencode

from app.providers import eastmoney_boards as em

PoolKind = Literal["limit_up", "broken", "limit_down"]

_POOL_PATHS: dict[PoolKind, tuple[str, str, str]] = {
    "limit_up": ("getTopicZTPool", "wz.ztzt", "fbt:asc"),
    "broken": ("getTopicZBPool", "wz.ztzt", "fbt:asc"),
    "limit_down": ("getTopicDTPool", "wz.ztzt", "fund:asc"),
}
_POOL_BASE = "https://push2ex.eastmoney.com/"
_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_PRICE_SCALE = Decimal(1000)


@dataclass(frozen=True)
class PoolStock:
    code: str
    exchange: str | None
    name: str
    price: Decimal | None
    change_pct: Decimal | None
    amount: Decimal | None
    float_cap: Decimal | None
    total_cap: Decimal | None
    turnover_pct: Decimal | None
    streak: int | None
    first_seal: str | None
    last_seal: str | None
    seal_fund: Decimal | None
    breaks: int | None
    industry: str | None
    limit_days: int | None
    limit_times: int | None


@dataclass(frozen=True)
class PoolPage:
    trade_date: date | None
    stocks: tuple[PoolStock, ...]


@dataclass(frozen=True)
class Distribution:
    trade_date: date | None
    buckets: dict[int, int]


@dataclass(frozen=True)
class OrgSurvey:
    code: str
    name: str
    notice_date: date
    receive_date: date | None
    institutions: int
    receive_way: str | None
    receive_object: str | None


@dataclass(frozen=True)
class SurveyPage:
    pages: int
    rows: tuple[OrgSurvey, ...]


def pool_url(kind: PoolKind, day: date) -> str:
    path, dpt, sort = _POOL_PATHS[kind]
    query = urlencode(
        {
            "ut": em.EASTMONEY_UT,
            "dpt": dpt,
            "Pageindex": "0",
            "pagesize": "1000",
            "sort": sort,
            "date": day.strftime("%Y%m%d"),
        }
    )
    return f"{_POOL_BASE}{path}?{query}"


def fenbu_url() -> str:
    return f"{_POOL_BASE}getTopicZDFenBu?{urlencode({'ut': em.EASTMONEY_UT, 'dpt': 'wz.ztzt'})}"


def parse_fenbu(payload: bytes) -> Distribution:
    document = em._object(em._decode(payload, "涨跌分布"), "涨跌分布")
    data = document.get("data")
    if data is None:
        return Distribution(trade_date=None, buckets={})
    body = em._object(data, "涨跌分布")
    buckets: dict[int, int] = {}
    for raw in em._list(body.get("fenbu") or [], "涨跌分布"):
        for key, value in em._object(raw, "涨跌分布").items():
            count = em._int(value)
            try:
                bucket = int(key)
            except ValueError:
                continue
            if count is not None and count >= 0 and -11 <= bucket <= 11:
                buckets[bucket] = buckets.get(bucket, 0) + count
    return Distribution(trade_date=_compact_day(body.get("qdate")), buckets=buckets)


def survey_url(since: date, page: int, page_size: int = 500) -> str:
    query = urlencode(
        {
            "reportName": "RPT_ORG_SURVEYNEW",
            "columns": (
                "SECURITY_CODE,SECURITY_NAME_ABBR,NOTICE_DATE,RECEIVE_START_DATE,SUM,"
                "RECEIVE_WAY_EXPLAIN,RECEIVE_OBJECT"
            ),
            "sortColumns": "NOTICE_DATE,SUM",
            "sortTypes": "-1,-1",
            "pageSize": str(page_size),
            "pageNumber": str(page),
            "source": "WEB",
            "client": "WEB",
            "filter": f'(NUMBERNEW="1")(IS_SOURCE="1")(NOTICE_DATE>=\'{since.isoformat()}\')',
        }
    )
    return f"{_DATACENTER_URL}?{query}"


def parse_pool(payload: bytes) -> PoolPage:
    document = em._object(em._decode(payload, "涨停股池"), "涨停股池")
    data = document.get("data")
    if data is None:
        return PoolPage(trade_date=None, stocks=())
    body = em._object(data, "涨停股池")
    stocks: list[PoolStock] = []
    for raw in em._list(body.get("pool") or [], "涨停股池"):
        row = em._object(raw, "涨停股池")
        code = em._text(row.get("c"))
        name = em._text(row.get("n"))
        if code is None or name is None:
            continue
        price = em._number(row.get("p"))
        stats = row.get("zttj")
        stat = em._object(stats, "涨停股池") if isinstance(stats, dict) else {}
        stocks.append(
            PoolStock(
                code=code,
                exchange=em._exchange(row.get("m")),
                name=name,
                price=None if price is None else price / _PRICE_SCALE,
                change_pct=_rounded(em._number(row.get("zdp"))),
                amount=em._number(row.get("amount")),
                float_cap=_rounded(em._number(row.get("ltsz"))),
                total_cap=_rounded(em._number(row.get("tshare"))),
                turnover_pct=_rounded(em._number(row.get("hs"))),
                streak=em._int(row.get("lbc")),
                first_seal=_clock(row.get("fbt")),
                last_seal=_clock(row.get("lbt")),
                seal_fund=em._number(row.get("fund")),
                breaks=em._int(row.get("zbc")),
                industry=em._text(row.get("hybk")),
                limit_days=em._int(stat.get("days")),
                limit_times=em._int(stat.get("ct")),
            )
        )
    return PoolPage(trade_date=_compact_day(body.get("qdate")), stocks=tuple(stocks))


def parse_surveys(payload: bytes) -> SurveyPage:
    document = em._object(em._decode(payload, "机构调研"), "机构调研")
    result = document.get("result")
    if result is None:
        return SurveyPage(pages=0, rows=())
    body = em._object(result, "机构调研")
    rows: list[OrgSurvey] = []
    for raw in em._list(body.get("data") or [], "机构调研"):
        row = em._object(raw, "机构调研")
        code = em._text(row.get("SECURITY_CODE"))
        name = em._text(row.get("SECURITY_NAME_ABBR"))
        notice = em._day(row.get("NOTICE_DATE"))
        if code is None or name is None or notice is None:
            continue
        rows.append(
            OrgSurvey(
                code=code,
                name=name,
                notice_date=notice,
                receive_date=em._day(row.get("RECEIVE_START_DATE")),
                institutions=em._int(row.get("SUM")) or 0,
                receive_way=em._text(row.get("RECEIVE_WAY_EXPLAIN")),
                receive_object=em._text(row.get("RECEIVE_OBJECT")),
            )
        )
    return SurveyPage(pages=em._int(body.get("pages")) or 0, rows=tuple(rows))


def _clock(value: object) -> str | None:
    number = em._int(value)
    if number is None or number < 0:
        return None
    text = f"{number:06d}"
    return f"{text[:2]}:{text[2:4]}:{text[4:]}"


def _compact_day(value: object) -> date | None:
    number = em._int(value)
    if number is None:
        return None
    try:
        text = str(number)
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _rounded(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(Decimal("0.01"))
