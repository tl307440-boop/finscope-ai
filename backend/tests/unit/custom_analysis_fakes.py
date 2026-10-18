"""自定义数据分析测试共用的内存仓库与假网关：不联网、不连库。"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.providers.errors import ProviderError
from app.providers.exchange_options import OptionStat
from app.repositories.custom_analysis import (
    OptionSummary,
    SeriesPointIn,
    SeriesSummary,
    StoredPoint,
)
from app.services.custom_analysis_rules import OptionRow, QualityResult

NOW = datetime(2026, 10, 16, 11, 0, tzinfo=UTC)  # 北京时间 19:00，已收盘
TODAY = date(2026, 10, 16)


def weekdays(end: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


@dataclass
class _StoredOption:
    stat: OptionStat
    quality: QualityResult


class MemoryStore:
    """同时满足同步（Store）与读取（Reader）两个协议。"""

    def __init__(self) -> None:
        self.options: dict[tuple[str, str, date], _StoredOption] = {}
        self.points: dict[tuple[str, date], tuple[SeriesPointIn, datetime]] = {}

    # ---- 写
    def upsert_option_stats(
        self, rows: Sequence[tuple[OptionStat, QualityResult]], fetched_at: datetime
    ) -> int:
        kept = [(stat, quality) for stat, quality in rows if quality.flag != "rejected"]
        for stat, quality in kept:
            self.options[(stat.exchange, stat.underlying_code, stat.trade_date)] = _StoredOption(
                stat, quality
            )
        return len(kept)

    def upsert_points(
        self, series_code: str, points: Sequence[SeriesPointIn], fetched_at: datetime
    ) -> int:
        for point in points:
            self.points[(series_code, point.trade_date)] = (point, fetched_at)
        return len(points)

    # ---- 读
    def option_days(self, exchange: str) -> set[date]:
        return {day for (ex, _code, day) in self.options if ex == exchange}

    def series_dates(self, series_code: str) -> set[date]:
        return {day for (code, day) in self.points if code == series_code}

    def series(self, series_code: str, start: date | None = None) -> list[StoredPoint]:
        out = [
            StoredPoint(day, point.value, point.quality_flag, point.meta)
            for (code, day), (point, _fetched) in sorted(self.points.items())
            if code == series_code and (start is None or day >= start)
        ]
        return out

    def option_rows(
        self, members: Sequence[tuple[str, str]], start: date | None = None
    ) -> list[OptionRow]:
        wanted = set(members)
        rows: list[OptionRow] = []
        for (exchange, code, day), stored in sorted(self.options.items(), key=lambda i: i[0][2]):
            if (exchange, code) in wanted and (start is None or day >= start):
                stat = stored.stat
                rows.append(
                    OptionRow(
                        day,
                        exchange,
                        code,
                        stat.call_volume,
                        stat.put_volume,
                        stat.call_oi,
                        stat.put_oi,
                        stored.quality.flag,
                    )
                )
        return rows

    def option_summaries(self) -> list[OptionSummary]:
        out: list[OptionSummary] = []
        for exchange in sorted({key[0] for key in self.options}):
            items = [item for key, item in self.options.items() if key[0] == exchange]
            days = [item.stat.trade_date for item in items]
            out.append(
                OptionSummary(
                    exchange=exchange,
                    rows=len(items),
                    first_date=min(days),
                    last_date=max(days),
                    review_rows=sum(1 for item in items if item.quality.flag != "ok"),
                    fetched_at=NOW,
                    source=items[0].stat.source,
                )
            )
        return out

    def option_review_notes(self, limit: int = 20) -> list[tuple[date, str, str, str]]:
        notes = [
            (key[2], key[0], key[1], item.quality.note or "")
            for key, item in self.options.items()
            if item.quality.flag != "ok"
        ]
        return sorted(notes, reverse=True)[:limit]

    def series_summaries(self) -> dict[str, SeriesSummary]:
        out: dict[str, SeriesSummary] = {}
        for code in {key[0] for key in self.points}:
            entries = [(key[1], value[0]) for key, value in self.points.items() if key[0] == code]
            days = [day for day, _ in entries]
            out[code] = SeriesSummary(
                series_code=code,
                rows=len(entries),
                first_date=min(days),
                last_date=max(days),
                review_rows=sum(1 for _, point in entries if point.quality_flag != "ok"),
                fetched_at=NOW,
                source=entries[0][1].source,
            )
        return out

    def series_review_notes(self, limit: int = 20) -> list[tuple[date, str, str]]:
        notes = [
            (key[1], key[0], str((value[0].meta or {}).get("note") or ""))
            for key, value in self.points.items()
            if value[0].quality_flag != "ok"
        ]
        return sorted(notes, reverse=True)[:limit]


Handler = Callable[[str], bytes | None]


class FakeGateway:
    """按 URL 子串分派的假网关；handler 返回 None 表示这个 URL 没有对应响应。"""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.routes: list[tuple[str, Callable[[str], bytes]]] = []

    def route(self, needle: str, handler: Callable[[str], bytes]) -> None:
        self.routes.append((needle, handler))

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        for needle, handler in self.routes:
            if needle in url:
                return handler(url)
        raise AssertionError(f"没有为这个 URL 配置响应：{url}")

    def count(self, needle: str) -> int:
        return sum(1 for url in self.calls if needle in url)


# ---- 载荷构造 -----------------------------------------------------------


def csindex_payload(
    days: Sequence[date], code: str, start: date | None = None, end: date | None = None
) -> bytes:
    """按起止日期取行；与真实接口一样，startDate 不是交易日时会回显一条同日期的幽灵行。"""

    def row(day: date, index: int) -> dict[str, object]:
        return {
            "tradeDate": day.strftime("%Y%m%d"),
            "indexCode": code,
            "close": 4000 + index,
            "peg": 12.5 + index * 0.01,
        }

    rows = [
        row(day, index)
        for index, day in enumerate(days)
        if (start is None or day >= start) and (end is None or day <= end)
    ]
    if start is not None and start not in days:
        rows.insert(0, row(start, 0))
    return json.dumps({"code": "200", "msg": "Success", "data": rows}).encode()


TREASURY_PAGE_SIZE = 500  # 东财数据中心的单页上限


def treasury_payload(days: Sequence[date], page: int = 1) -> bytes:
    """降序分页，单页最多 500 行，与真实接口一致。"""

    ordered = sorted(days, reverse=True)
    pages = max(1, -(-len(ordered) // TREASURY_PAGE_SIZE))
    window = ordered[(page - 1) * TREASURY_PAGE_SIZE : page * TREASURY_PAGE_SIZE]
    rows = [
        {
            "SOLAR_DATE": f"{day.isoformat()} 00:00:00",
            "EMM00166462": 1.4,
            "EMM00166466": 1.65,
            "EMG00001306": 4.0,
            "EMG00001310": 4.2,
        }
        for day in window
    ]
    return json.dumps({"result": {"pages": pages, "count": len(ordered), "data": rows}}).encode()


def gdp_payload() -> bytes:
    rows = [
        ("2025-03-01", "2025年第1季度", 300_000),
        ("2025-06-01", "2025年第1-2季度", 650_000),
        ("2025-09-01", "2025年第1-3季度", 1_000_000),
        ("2025-12-01", "2025年", 1_400_000),
        ("2026-03-01", "2026年第1季度", 330_000),
        ("2026-06-01", "2026年第1-2季度", 700_000),
    ]
    data = [
        {"REPORT_DATE": f"{day} 00:00:00", "TIME": label, "DOMESTICL_PRODUCT_BASE": value}
        for day, label, value in rows
    ]
    return json.dumps({"result": {"pages": 1, "count": len(data), "data": data}}).encode()


def cross_section_payload(day: date, stocks: int = 3100, *, count: int | None = None) -> bytes:
    rows = []
    for index in range(stocks):
        rows.append(
            {
                "SECURITY_CODE": f"{index:06d}",
                "SECURITY_NAME_ABBR": "*ST样本" if index % 50 == 0 else f"样本{index}",
                "TOTAL_MARKET_CAP": 30_000_000_000 + index,
                "PB_MRQ": 0.5 + (index % 40) / 10,
                "TRADE_DATE": f"{day.isoformat()} 00:00:00",
            }
        )
    return json.dumps(
        {"result": {"pages": 1, "count": stocks if count is None else count, "data": rows}}
    ).encode()


def listing_payload(stocks: int = 3100) -> bytes:
    rows = [
        {"SECURITY_CODE": f"{index:06d}", "LISTING_DATE": "2005-01-04 00:00:00"}
        for index in range(stocks)
    ]
    return json.dumps({"result": {"pages": 1, "count": stocks, "data": rows}}).encode()


def sse_payload(day: date) -> bytes:
    rows = [
        {
            "SECURITY_CODE": code,
            "SECURITY_ABBR": "标的",
            "TRADE_DATE": day.isoformat(),
            "PUT_VOLUME": "400",
            "CALL_VOLUME": "600",
            "LEAVES_PUT_QTY": "500",
            "LEAVES_CALL_QTY": "800",
            "CP_RATE": "66.67",
            "TOTAL_VOLUME": "1,000",
        }
        for code in ("510050", "510300", "510500", "588000", "588080")
    ]
    return ("cb(" + json.dumps({"result": rows}) + ")").encode()


def szse_payload(day: date) -> bytes:
    rows = [
        {
            "bddm": code,
            "bdmc": "标的",
            "cjl": "300",
            "rccjl": "200",
            "rpcjl": "100",
            "rcrpccb": "50.00",
            "wpchyzs": "300",
            "wpcrchys": "200",
            "wpcrphys": "100",
        }
        for code in ("159919", "159922")
    ]
    return json.dumps([{"metadata": {}, "data": rows}]).encode()


def cffex_payload(day: date) -> bytes:
    header = (
        "合约代码,今开盘,最高价,最低价,成交量,成交金额,持仓量,持仓变化,"
        "今收盘,今结算,前结算,涨跌1,涨跌2,Delta"
    )
    lines = [
        "HO2610-C-2500,1,1,1,10,1,20,0,1,1,1,0,0,--",
        "HO2610-P-2500,1,1,1,8,1,16,0,1,1,1,0,0,--",
        "IO2610-C-4000,1,1,1,100,1,200,0,1,1,1,0,0,--",
        "IO2610-P-4000,1,1,1,80,1,160,0,1,1,1,0,0,--",
        "MO2610-C-6000,1,1,1,1000,1,2000,0,1,1,1,0,0,--",
        "MO2610-P-6000,1,1,1,900,1,1800,0,1,1,1,0,0,--",
    ]
    return ("\n".join([header, *lines]) + "\n").encode("gbk")


def build_gateway(calendar: Sequence[date]) -> FakeGateway:
    """一个完整可用的假上游：所有来源都按 calendar 给数据。"""

    gateway = FakeGateway()

    def day_from(url: str, key: str, width: int = 8) -> date:
        raw = url.split(f"{key}=")[1][:width]
        if width == 8:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        return date.fromisoformat(raw)

    gateway.route(
        "csindex.com.cn",
        lambda url: csindex_payload(
            calendar,
            url.split("indexCode=")[1].split("&")[0],
            day_from(url, "startDate"),
            day_from(url, "endDate"),
        ),
    )
    gateway.route(
        "RPTA_WEB_TREASURYYIELD",
        lambda url: treasury_payload(calendar, int(url.split("pageNumber=")[1].split("&")[0])),
    )
    gateway.route("RPT_ECONOMY_GDP", lambda url: gdp_payload())
    gateway.route("RPT_F10_ORG_BASICINFO", lambda url: listing_payload())
    gateway.route(
        "RPT_VALUEANALYSIS_DET",
        lambda url: cross_section_payload(
            date.fromisoformat(url.split("TRADE_DATE%3D%27")[1][:10])
            if "TRADE_DATE%3D%27" in url
            else TODAY
        ),
    )
    gateway.route("query.sse.com.cn", lambda url: sse_payload(day_from(url, "tradeDate")))
    gateway.route("szse.cn", lambda url: szse_payload(day_from(url, "txtQueryDate", 10)))
    gateway.route("cffex.com.cn", lambda url: cffex_payload(TODAY))
    return gateway


def failing(code: str = "UPSTREAM_DISCONNECTED", status: int = 502) -> Callable[[str], bytes]:
    def raise_error(url: str) -> bytes:
        raise ProviderError(code=code, message="上游失败", status_code=status)

    return raise_error


__all__ = [
    "NOW",
    "TODAY",
    "FakeGateway",
    "MemoryStore",
    "build_gateway",
    "cffex_payload",
    "cross_section_payload",
    "csindex_payload",
    "failing",
    "gdp_payload",
    "listing_payload",
    "sse_payload",
    "szse_payload",
    "treasury_payload",
    "weekdays",
]
