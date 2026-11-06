"""A 股全景看板用到的公开数据源：只构造 URL / 请求头 / 签名，不发请求、不解析。

口径（2026-11 实测，全部免登录）：
- 东财 push2：`ulist.np`（批量报价，f6 为成交额元）、`clist`（榜单，单页最多 100 条）、
  `stock/get`（个股详情，f129 为概念板块）；push2his：`trends2`（分时）、`kline`（日 K）。
- 东财 push2ex：涨停池 / 炸板池 / 跌停池 / 昨日涨停池
  （昨日池里的 zdp 是「今日涨跌幅」，ylbc 是昨日连板数）。
- 东财数据中心：两融 `RPTA_RZRQ_LSDB`、期指席位 `RPT_FUTU_DAILYPOSITION`、龙虎榜 `RPT_*BILLBOARD*`。
- 同花顺：涨停池 `limit_up_pool`（含涨停原因 reason_type）与板块涨停统计 `block_top`。
- 资讯：财联社电报（sign = md5(sha1(排序后的参数串))）、东财 7x24、同花顺 7x24、金十快讯。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal
from urllib.parse import urlencode

from app.providers import eastmoney_boards as em

EASTMONEY_UT = em.EASTMONEY_UT
# push2 / push2his 个股与榜单接口用的另一枚公开 ut（东财网页端写死在前端脚本里）。
QUOTE_UT = "fa5fd1943c7b386f172d6893dbfba10b"
LIST_UT = "bd1d9ddb04089700cf9c27f6f7426281"

PAGE_SIZE = em.PAGE_SIZE  # 东财单页最多 100 条，再大会被拒绝。

EM_HEADERS: Mapping[str, str] = em.EASTMONEY_HEADERS
THS_DATA_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://data.10jqka.com.cn/datacenterph/limitup/limtupInfo.html",
}
THS_NEWS_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://news.10jqka.com.cn/realtimenews.html",
}
CLS_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "Referer": "https://www.cls.cn/telegraph",
}
JIN10_HEADERS: Mapping[str, str] = {
    "User-Agent": "Mozilla/5.0 (FinScope research)",
    "x-app-id": "bVBF4FyRTn5NJF5n",
    "x-version": "1.0.0",
    "Origin": "https://www.jin10.com",
    "Referer": "https://www.jin10.com/",
}

_PUSH2 = "https://push2.eastmoney.com/api/qt"
_PUSH2HIS = "https://push2his.eastmoney.com/api/qt/stock"
_PUSH2EX = "https://push2ex.eastmoney.com/"
_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_THS_DATA = "https://data.10jqka.com.cn/dataapi/limit_up"

AShareFs = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048"
CONCEPT_BOARD_FS = "m:90 t:3 f:!50"

CLS_APP = {"app": "CailianpressWeb", "os": "web", "sv": "8.4.6"}


def ulist_url(
    secids: Sequence[str], fields: str = "f2,f3,f4,f6,f8,f12,f13,f14,f15,f16,f17,f18,f21"
) -> str:
    query = urlencode(
        {"fltt": "2", "invt": "2", "fields": fields, "secids": ",".join(secids), "ut": EASTMONEY_UT}
    )
    return f"{_PUSH2}/ulist.np/get?{query}"


def trends_url(secid: str, ndays: int = 1) -> str:
    query = urlencode(
        {
            "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
            "secid": secid,
            "ndays": str(ndays),
            "iscr": "0",
            "ut": QUOTE_UT,
        }
    )
    return f"{_PUSH2HIS}/trends2/get?{query}"


def daily_kline_url(secid: str, limit: int) -> str:
    query = urlencode(
        {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "secid": secid,
            "klt": "101",
            "fqt": "1",
            "lmt": str(limit),
            "end": "20500101",
            "ut": QUOTE_UT,
        }
    )
    return f"{_PUSH2HIS}/kline/get?{query}"


def clist_url(fs: str, fid: str, page: int, fields: str, descending: bool = True) -> str:
    query = urlencode(
        {
            "pn": str(page),
            "pz": str(PAGE_SIZE),
            "po": "1" if descending else "0",
            "np": "1",
            "ut": LIST_UT,
            "fltt": "2",
            "invt": "2",
            "fid": fid,
            "fs": fs,
            "fields": fields,
        }
    )
    return f"{_PUSH2}/clist/get?{query}"


def stock_get_url(secid: str) -> str:
    query = urlencode(
        {
            "secid": secid,
            "ut": QUOTE_UT,
            "fltt": "2",
            "invt": "2",
            "fields": "f43,f44,f45,f46,f47,f48,f57,f58,f60,f116,f117,f127,f128,f129,f168,f170",
        }
    )
    return f"{_PUSH2}/stock/get?{query}"


def yesterday_pool_url(day: date) -> str:
    query = urlencode(
        {
            "ut": EASTMONEY_UT,
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "1000",
            "sort": "zdp:desc",
            "date": day.strftime("%Y%m%d"),
        }
    )
    return f"{_PUSH2EX}getYesterdayZTPool?{query}"


def margin_url(rows: int) -> str:
    query = urlencode(
        {
            "reportName": "RPTA_RZRQ_LSDB",
            "columns": "ALL",
            "source": "WEB",
            "client": "WEB",
            "sortColumns": "DIM_DATE",
            "sortTypes": "-1",
            "pageNumber": "1",
            "pageSize": str(rows),
        }
    )
    return f"{_DATACENTER}?{query}"


def _datacenter(report: str, **extra: str) -> str:
    query = {"reportName": report, "columns": "ALL", "source": "WEB", "client": "WEB", **extra}
    return f"{_DATACENTER}?{urlencode(query)}"


def futures_latest_url() -> str:
    return _datacenter(
        "RPT_FUTU_DAILYPOSITION",
        columns="TRADE_DATE",
        filter='(TRADE_MARKET_CODE="069001009")',
        sortColumns="TRADE_DATE",
        sortTypes="-1",
        pageNumber="1",
        pageSize="1",
    )


def futures_url(day: date, page: int) -> str:
    return _datacenter(
        "RPT_FUTU_DAILYPOSITION",
        columns=(
            "SECURITY_CODE,TRADE_DATE,MEMBER_NAME_ABBR,LONG_POSITION,LP_CHANGE,"
            "SHORT_POSITION,SP_CHANGE"
        ),
        filter=f'(TRADE_MARKET_CODE="069001009")(TRADE_DATE=\'{day.isoformat()}\')(TYPE="0")',
        sortColumns="SECURITY_CODE,LPRANK",
        sortTypes="1,1",
        pageNumber=str(page),
        pageSize="500",
    )


def lhb_latest_url() -> str:
    return _datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        columns="TRADE_DATE",
        sortColumns="TRADE_DATE",
        sortTypes="-1",
        pageNumber="1",
        pageSize="1",
    )


LhbKind = Literal["stocks", "buy", "sell"]
_LHB_REPORTS: dict[LhbKind, str] = {
    "stocks": "RPT_DAILYBILLBOARD_DETAILSNEW",
    "buy": "RPT_BILLBOARD_DAILYDETAILSBUY",
    "sell": "RPT_BILLBOARD_DAILYDETAILSSELL",
}


def lhb_url(kind: LhbKind, day: date, page: int = 1, code: str | None = None) -> str:
    flt = f"(TRADE_DATE='{day.isoformat()}')"
    if code is not None:
        flt += f'(SECURITY_CODE="{code}")'
    return _datacenter(
        _LHB_REPORTS[kind],
        filter=flt,
        sortColumns="SECURITY_CODE",
        sortTypes="1",
        pageNumber=str(page),
        pageSize="500",
    )


def lhb_stock_latest_url(code: str) -> str:
    return _datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        columns="TRADE_DATE",
        filter=f'(SECURITY_CODE="{code}")',
        sortColumns="TRADE_DATE",
        sortTypes="-1",
        pageNumber="1",
        pageSize="1",
    )


def ths_pool_url(day: date, page: int = 1) -> str:
    query = urlencode(
        {
            "page": str(page),
            "limit": "200",
            "field": (
                "199112,10,9001,330323,330324,330325,9002,330329,"
                "133971,133970,1968584,3475914,9003,9004"
            ),
            "filter": "HS,GEM2STAR",
            "order_field": "330324",
            "order_type": "0",
            "date": day.strftime("%Y%m%d"),
        }
    )
    return f"{_THS_DATA}/limit_up_pool?{query}"


def ths_block_url(day: date) -> str:
    query = urlencode({"filter": "HS,GEM2STAR", "date": day.strftime("%Y%m%d")})
    return f"{_THS_DATA}/block_top?{query}"


def cls_sign(query: str) -> str:
    """财联社网页端签名：对排好序的参数串先 sha1 再 md5。"""

    return hashlib.md5(hashlib.sha1(query.encode()).hexdigest().encode()).hexdigest()


def cls_telegraph_url(rows: int, *, category: str = "", now: int | None = None) -> str:
    params = {
        **CLS_APP,
        "category": category,
        "rn": str(rows),
        "refresh_type": "1",
        "last_time": str(now if now is not None else int(time.time())),
    }
    query = urlencode(sorted(params.items()))
    return f"https://www.cls.cn/v1/roll/get_roll_list?{query}&sign={cls_sign(query)}"


def em_news_url(size: int) -> str:
    query = urlencode(
        {
            "client": "web",
            "biz": "web_724",
            "fastColumn": "102",
            "sortEnd": "",
            "pageSize": str(size),
            "req_trace": "finscope",
        }
    )
    return f"https://np-listapi.eastmoney.com/comm/web/getFastNewsList?{query}"


def ths_news_url(size: int) -> str:
    query = urlencode({"page": "1", "tag": "", "track": "website", "pagesize": str(size)})
    return f"https://news.10jqka.com.cn/tapp/news/push/stock/?{query}"


def jin10_url() -> str:
    return (
        f"https://flash-api.jin10.com/get_flash_list?{urlencode({'channel': '-8200', 'vip': '1'})}"
    )
