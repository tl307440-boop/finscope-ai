"""总览驾驶舱原型的数据快照抓取脚本（只用标准库）。

运行：python docs/prototypes/dashboard/fetch_snapshot.py
产物：同目录 snapshot.js（window.SNAPSHOT = {...}），index.html 直接读它，file:// 打开即可。

只抓公开匿名接口的原始字段，不做任何补数：某个上游失败就把错误写进 errors，页面显示不可用。
所有评分（市场温度、全球流动性）都在 index.html 里按提示词同一套公式现算，便于核对。
"""

from __future__ import annotations

import csv
import io
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

OUT = Path(__file__).with_name("snapshot.js")
EM_UT = "7eea3edcaed734bea9cbfc24409ed989"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FinScope-prototype"
errors: list[dict[str, str]] = []
sources: dict[str, str] = {}


def get(url: str, *, referer: str | None = None, encoding: str = "utf-8", timeout: int = 15) -> str:
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode(encoding, errors="replace")


def guarded(key: str, source: str, fn: Any) -> Any:
    try:
        value = fn()
        sources[key] = source
        return value
    except Exception as exc:  # noqa: BLE001 原型脚本：任何失败都只记录，不补数
        errors.append({"block": key, "source": source, "error": f"{type(exc).__name__}: {exc}"})
        print(f"[失败] {key}: {exc}", file=sys.stderr)
        return None


def num(text: str) -> float | None:
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value


# ---------- A 股指数：腾讯行情 ----------
A_INDICES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
    ("sh000688", "科创50"),
    ("sh000300", "沪深300"),
    ("sh000905", "中证500"),
    ("sh000852", "中证1000"),
    ("bj899050", "北证50"),
    ("sh000985", "中证全指"),
]


def tencent_quotes(codes: list[str]) -> dict[str, list[str]]:
    text = get("https://qt.gtimg.cn/q=" + ",".join(codes), encoding="gbk")
    found: dict[str, list[str]] = {}
    for match in re.finditer(r'v_([a-zA-Z0-9_.$]+)="([^"]*)"', text):
        parts = match.group(2).split("~")
        if len(parts) > 40:
            found[match.group(1)] = parts
    return found


def a_indices() -> list[dict[str, Any]]:
    quotes = tencent_quotes([code for code, _ in A_INDICES])
    rows = []
    for code, name in A_INDICES:
        p = quotes.get(code)
        if p is None:
            continue
        last, prev = num(p[3]), num(p[4])
        rows.append(
            {
                "code": code,
                "name": name,
                "last": last,
                "prev_close": prev,
                "open": num(p[5]),
                "high": num(p[33]),
                "low": num(p[34]),
                # 腾讯字段 37 为成交额，单位万元
                "amount_wan": num(p[37]),
                "quote_time": p[30],
            }
        )
    return rows


def tencent_minutes(code: str) -> list[list[Any]]:
    payload = json.loads(get(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={code}"))
    lines = payload["data"][code]["data"]["data"]
    rows = []
    for line in lines:
        hhmm, price, cum_vol, cum_amt = line.split(" ")[:4]
        rows.append([f"{hhmm[:2]}:{hhmm[2:]}", float(price), float(cum_amt)])
    return rows


def tencent_kline(code: str, count: int = 260) -> list[list[Any]]:
    url = (
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get"
        f"?param={code},day,,,{count},"
    )
    payload = json.loads(get(url))
    block = payload["data"][code]
    days = block.get("day") or block.get("qfqday") or []
    rows = []
    for item in days:
        # [日期, 开, 收, 高, 低, 成交量(手), {}, 换手, 成交额(万元), ...]
        amount = num(item[8]) if len(item) > 8 else None
        rows.append([item[0], num(item[1]), num(item[2]), num(item[3]), num(item[4]), num(item[5]), amount])
    return rows


# ---------- 涨跌分布与涨停股池：东财 push2ex ----------
def em_fenbu() -> dict[str, Any]:
    payload = json.loads(get(f"https://push2ex.eastmoney.com/getTopicZDFenBu?ut={EM_UT}&dpt=wz.ztzt"))
    data = payload["data"]
    buckets: dict[str, int] = {}
    for item in data["fenbu"]:
        for key, value in item.items():
            buckets[key] = int(value)
    return {"qdate": str(data["qdate"]), "buckets": buckets}


def em_pool(kind: str, day: str) -> dict[str, Any]:
    path, sort = {
        "zt": ("getTopicZTPool", "fbt:asc"),
        "zb": ("getTopicZBPool", "fbt:asc"),
        "dt": ("getTopicDTPool", "fund:asc"),
    }[kind]
    url = (
        f"https://push2ex.eastmoney.com/{path}?ut={EM_UT}&dpt=wz.ztzt&Pageindex=0"
        f"&pagesize=1000&sort={sort}&date={day}"
    )
    payload = json.loads(get(url))
    data = payload.get("data") or {}
    rows = []
    for r in data.get("pool") or []:
        rows.append(
            {
                "code": r.get("c"),
                "name": r.get("n"),
                "price": (r.get("p") or 0) / 1000,
                "change_pct": round(r.get("zdp") or 0, 2),
                "amount": r.get("amount"),
                "float_cap": r.get("ltsz"),
                "turnover_pct": round(r.get("hs") or 0, 2),
                "streak": r.get("lbc"),
                "first_seal": r.get("fbt"),
                "seal_fund": r.get("fund"),
                "breaks": r.get("zbc"),
                "industry": r.get("hybk"),
            }
        )
    return {"qdate": str(data.get("qdate") or ""), "rows": rows}


# ---------- 热榜：同花顺 ----------
THS_REF = "https://eq.10jqka.com.cn/"


def ths_hot_stocks() -> list[dict[str, Any]]:
    url = "https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/stock?stock_type=a&type=hour&list_type=normal"
    data = json.loads(get(url, referer=THS_REF))["data"]["stock_list"]
    rows = []
    for r in data:
        tag = r.get("tag") or {}
        rows.append(
            {
                "rank": r.get("order"),
                "code": r.get("code"),
                "name": r.get("name"),
                "change_pct": r.get("rise_and_fall"),
                "heat": num(r.get("rate")),
                "rank_change": r.get("hot_rank_chg"),
                "popularity_tag": tag.get("popularity_tag"),
                "concepts": tag.get("concept_tag") or [],
                "analysis": (r.get("analyse") or "")[:160],
            }
        )
    return rows


def ths_hot_plates(kind: str) -> list[dict[str, Any]]:
    url = f"https://dq.10jqka.com.cn/fuyao/hot_list_data/out/hot_list/v1/plate?type={kind}"
    data = json.loads(get(url, referer=THS_REF))["data"]["plate_list"]
    return [
        {
            "rank": r.get("order"),
            "code": r.get("code"),
            "name": r.get("name"),
            "change_pct": r.get("rise_and_fall"),
            "heat": num(r.get("rate")),
            "rank_change": r.get("hot_rank_chg"),
            "hot_tag": r.get("hot_tag"),
            "tag": r.get("tag"),
        }
        for r in data
    ]


# ---------- 板块涨幅与个股榜：新浪（东财 push2 在本机被拒，原型用新浪替代） ----------
SINA_REF = "https://finance.sina.com.cn/"


def sina_boards(url: str) -> list[dict[str, Any]]:
    text = get(url, referer=SINA_REF, encoding="gbk")
    body = text[text.index("{") : text.rindex("}") + 1]
    rows = []
    for value in json.loads(body).values():
        p = value.split(",")
        if len(p) < 13:
            continue
        rows.append(
            {
                "code": p[0],
                "name": p[1],
                "members": int(p[2]),
                "change_pct": num(p[5]),
                "amount": num(p[7]),
                "leader_symbol": p[8],
                "leader_change_pct": num(p[9]),
                "leader_name": p[12],
            }
        )
    return sorted(rows, key=lambda r: r["change_pct"] or -999, reverse=True)


def sina_rank(sort: str, num_rows: int = 40) -> list[dict[str, Any]]:
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page=1&num={num_rows}&sort={sort}&asc=0&node=hs_a&symbol=&_s_r_a=page"
    )
    data = json.loads(get(url, referer=SINA_REF))
    return [
        {
            "symbol": r["symbol"],
            "code": r["code"],
            "name": r["name"],
            "last": num(r["trade"]),
            "change_pct": r["changepercent"],
            "amount": r["amount"],
            "turnover_pct": r.get("turnoverratio"),
            "market_cap_wan": r.get("mktcap"),
        }
        for r in data
    ]


# ---------- 公告：东财公告中心（全市场） ----------
def em_announcements(pages: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        url = (
            "https://np-anotice-stock.eastmoney.com/api/security/ann?page_size=100"
            f"&page_index={page}&ann_type=A&client_source=web&f_node=0&s_node=0"
        )
        data = json.loads(get(url))["data"]["list"]
        for r in data:
            codes = r.get("codes") or []
            first = codes[0] if codes else {}
            columns = r.get("columns") or []
            rows.append(
                {
                    "art_code": r.get("art_code"),
                    "code": first.get("stock_code"),
                    "name": first.get("short_name"),
                    "title": r.get("title_ch") or r.get("title"),
                    "column": columns[0]["column_name"] if columns else None,
                    "notice_date": (r.get("notice_date") or "")[:10],
                    "display_time": (r.get("display_time") or "")[:19],
                    "url": f"https://data.eastmoney.com/notices/detail/{first.get('stock_code')}/{r.get('art_code')}.html",
                }
            )
        time.sleep(0.3)
    return rows


# ---------- 宏观：FRED 公开 CSV / 东财数据中心 / 腾讯外汇 / 新浪期货 ----------
FRED_IDS = [
    "WALCL",
    "WTREGEN",
    "RRPONTSYD",
    "DTWEXBGS",
    "DFII10",
    "BAMLH0A0HYM2",
    "NFCI",
    "VIXCLS",
    "DGS10",
    "DGS2",
    "T10Y2Y",
    "FEDFUNDS",
    "SOFR",
    "CPIAUCSL",
    "UNRATE",
    "PAYEMS",
]


def fred(series_id: str, keep_days: int = 620) -> list[list[Any]]:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        text = get(url, timeout=20)
    except OSError:
        # FRED 对 urllib 偶发不响应；改用系统 curl 重试一次
        done = subprocess.run(["curl", "-s", "--max-time", "60", url], capture_output=True, check=True)
        text = done.stdout.decode("utf-8")
        if not text.startswith("observation_date"):
            raise ValueError("FRED 返回不是 CSV") from None
    cutoff = (datetime.now(UTC) - timedelta(days=keep_days)).date().isoformat()
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 2 or row[0] == "observation_date" or row[0] < cutoff:
            continue
        value = num(row[1])
        if value is not None:  # FRED 用 "." 表示当天无观测，跳过而不是补 0
            rows.append([row[0], value])
    return rows


DC = "https://datacenter-web.eastmoney.com/api/data/v1/get"
DC_REF = "https://data.eastmoney.com/"


def dc(params: str) -> list[dict[str, Any]]:
    payload = json.loads(get(f"{DC}?{params}&source=WEB&client=WEB", referer=DC_REF))
    return (payload.get("result") or {}).get("data") or []


def shibor(indicator: str) -> list[list[Any]]:
    data = dc(
        "reportName=RPT_IMP_INTRESTRATEN&columns=REPORT_DATE,IR_RATE"
        f"&filter=(MARKET_CODE=%22001%22)(CURRENCY_CODE=%22CNY%22)(INDICATOR_ID=%22{indicator}%22)"
        "&sortColumns=REPORT_DATE&sortTypes=-1&pageSize=120&pageNumber=1"
    )
    return sorted([[r["REPORT_DATE"][:10], r["IR_RATE"]] for r in data])


def treasury() -> list[dict[str, Any]]:
    data = dc(
        "reportName=RPTA_WEB_TREASURYYIELD&columns=SOLAR_DATE,EMM00166462,EMM00166466,EMG00001306,EMG00001310"
        "&sortColumns=SOLAR_DATE&sortTypes=-1&pageSize=120&pageNumber=1"
    )
    return sorted(
        [
            {
                "date": r["SOLAR_DATE"][:10],
                "cn2y": r.get("EMM00166462"),
                "cn10y": r.get("EMM00166466"),
                "us2y": r.get("EMG00001306"),
                "us10y": r.get("EMG00001310"),
            }
            for r in data
        ],
        key=lambda r: r["date"],
    )


def china_pmi() -> list[list[Any]]:
    data = dc("reportName=RPT_ECONOMY_PMI&columns=REPORT_DATE,MAKE_INDEX&sortColumns=REPORT_DATE&sortTypes=-1&pageSize=24&pageNumber=1")
    return sorted([[r["REPORT_DATE"][:10], r["MAKE_INDEX"]] for r in data])


def stock_quotes(codes: list[str]) -> dict[str, dict[str, Any]]:
    """腾讯个股行情：3 最新价，4 昨收，39 市盈率(TTM)，44 流通市值(亿)，45 总市值(亿)。"""

    symbols = [("sh" if c.startswith("6") else "bj" if c[0] in "489" else "sz") + c for c in codes]
    quotes = tencent_quotes(symbols)
    found = {}
    for symbol, p in quotes.items():
        found[symbol[2:]] = {
            "name": p[1],
            "last": num(p[3]),
            "prev_close": num(p[4]),
            "pe_ttm": num(p[39]),
            "market_cap_yi": num(p[45]),
        }
    return found


WATCH_SAMPLE = ["600519", "300059", "300308", "002594", "300750", "601318", "688981", "000858"]


def watch_sample() -> list[dict[str, Any]]:
    quotes = stock_quotes(WATCH_SAMPLE)
    rows = []
    for code in WATCH_SAMPLE:
        symbol = ("sh" if code.startswith("6") else "sz") + code
        bars = tencent_kline(symbol, 12)
        quote = quotes.get(code, {})
        rows.append({"code": code, **quote, "closes": [[b[0], b[2]] for b in bars]})
        time.sleep(0.15)
    return rows


def margin() -> list[dict[str, Any]]:
    data = dc("reportName=RPTA_RZRQ_LSHJ&columns=DIM_DATE,RZYE,RQYE,RZJME&sortColumns=DIM_DATE&sortTypes=-1&pageSize=30&pageNumber=1")
    return sorted(
        [{"date": r["DIM_DATE"][:10], "rzye": r["RZYE"], "rqye": r["RQYE"], "rzjme": r["RZJME"]} for r in data],
        key=lambda r: r["date"],
    )


def usdcny() -> dict[str, Any]:
    # 外汇行情字段少于 40 个，不走 tencent_quotes；3 最新价，12 涨跌，13 涨跌幅%，末位日期
    text = get("https://qt.gtimg.cn/q=whUSDCNY", encoding="gbk")
    match = re.search(r'="([^"]*)"', text)
    if match is None:
        raise ValueError("whUSDCNY 无返回")
    parts = match.group(1).split("~")
    return {"last": num(parts[3]), "change": num(parts[12]), "change_pct": num(parts[13]), "date": parts[-1]}


GLOBAL_TENCENT = [("usINX", "标普500"), ("usIXIC", "纳斯达克"), ("usDJI", "道琼斯"), ("hkHSI", "恒生指数"), ("hkHSTECH", "恒生科技")]


def global_indices() -> list[dict[str, Any]]:
    quotes = tencent_quotes([code for code, _ in GLOBAL_TENCENT])
    rows = []
    for code, name in GLOBAL_TENCENT:
        p = quotes.get(code)
        if p is None:
            continue
        rows.append({"code": code, "name": name, "last": num(p[3]), "prev_close": num(p[4]), "quote_time": p[30]})
    text = get("https://hq.sinajs.cn/list=znb_NKY,hf_GC,hf_CL", referer=SINA_REF, encoding="gbk")
    for key, value in re.findall(r"hq_str_(\w+)=\"([^\"]*)\"", text):
        p = value.split(",")
        if key == "znb_NKY" and len(p) > 8:
            rows.append({"code": key, "name": "日经225", "last": num(p[1]), "prev_close": num(p[1]) - num(p[2]), "quote_time": f"{p[6]} {p[7]}"})
        elif key in ("hf_GC", "hf_CL") and len(p) > 12:
            # 新浪外盘期货：0 最新价，7 昨结算，12 日期
            rows.append({"code": key, "name": "COMEX黄金" if key == "hf_GC" else "NYMEX原油", "last": num(p[0]), "prev_close": num(p[7]), "quote_time": f"{p[12]} {p[6]}"})
    return rows


def main() -> None:
    snap: dict[str, Any] = {"generated_at": datetime.now(UTC).isoformat()}
    snap["indices"] = guarded("indices", "腾讯行情 qt.gtimg.cn", a_indices)
    snap["minutes"] = {}
    for code in ("sh000001", "sz399001", "sz399006", "sh000688"):
        snap["minutes"][code] = guarded(f"minutes.{code}", "腾讯分时 web.ifzq.gtimg.cn", lambda c=code: tencent_minutes(c))
    snap["klines"] = {}
    for code in ("sh000001", "sz399001", "sz399006", "sh000688", "sh000300", "sh000905", "sh000852", "bj899050", "sh000985"):
        snap["klines"][code] = guarded(f"klines.{code}", "腾讯日K proxy.finance.qq.com", lambda c=code: tencent_kline(c))
        time.sleep(0.15)
    fenbu = guarded("fenbu", "东财 涨跌分布 push2ex getTopicZDFenBu", em_fenbu)
    snap["fenbu"] = fenbu
    day = fenbu["qdate"] if fenbu else datetime.now().strftime("%Y%m%d")
    snap["pools"] = {kind: guarded(f"pool.{kind}", "东财 涨停板行情 push2ex", lambda k=kind: em_pool(k, day)) for kind in ("zt", "zb", "dt")}
    snap["ths_hot"] = guarded("ths_hot", "同花顺热榜 个股", ths_hot_stocks)
    snap["ths_industry"] = guarded("ths_industry", "同花顺热榜 行业", lambda: ths_hot_plates("industry"))
    snap["ths_concept"] = guarded("ths_concept", "同花顺热榜 概念", lambda: ths_hot_plates("concept"))
    snap["sina_industry"] = guarded("sina_industry", "新浪 行业板块", lambda: sina_boards("https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"))
    snap["sina_concept"] = guarded("sina_concept", "新浪 概念板块", lambda: sina_boards("https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param=class"))
    snap["gainers"] = guarded("gainers", "新浪 沪深A股涨幅榜", lambda: sina_rank("changepercent"))
    snap["by_amount"] = guarded("by_amount", "新浪 沪深A股成交额榜", lambda: sina_rank("amount"))
    snap["announcements"] = guarded("announcements", "东财 公告中心 np-anotice-stock", em_announcements)
    snap["fred"] = {}
    for series_id in FRED_IDS:
        snap["fred"][series_id] = guarded(f"fred.{series_id}", "FRED 公开 CSV fredgraph.csv", lambda s=series_id: fred(s))
    snap["shibor_on"] = guarded("shibor_on", "东财数据中心 SHIBOR", lambda: shibor("001"))
    snap["shibor_1w"] = guarded("shibor_1w", "东财数据中心 SHIBOR", lambda: shibor("101"))
    snap["treasury"] = guarded("treasury", "东财数据中心 中美国债收益率", treasury)
    snap["margin"] = guarded("margin", "东财数据中心 两融余额", margin)
    snap["usdcny"] = guarded("usdcny", "腾讯外汇 whUSDCNY", usdcny)
    snap["global"] = guarded("global", "腾讯行情 / 新浪外盘", global_indices)
    snap["china_pmi"] = guarded("china_pmi", "东财数据中心 制造业PMI", china_pmi)
    hot_codes = [row["code"] for row in (snap["ths_hot"] or [])[:30]]
    snap["hot_quotes"] = guarded("hot_quotes", "腾讯个股行情", lambda: stock_quotes(hot_codes)) if hot_codes else None
    snap["watch_sample"] = guarded("watch_sample", "腾讯个股行情 / 日K", watch_sample)
    snap["sources"] = sources
    snap["errors"] = errors
    OUT.write_text("window.SNAPSHOT = " + json.dumps(snap, ensure_ascii=False) + ";\n", encoding="utf-8")
    print(f"写入 {OUT}，失败 {len(errors)} 项")


if __name__ == "__main__":
    main()
