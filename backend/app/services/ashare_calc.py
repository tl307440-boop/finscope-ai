"""A 股全景看板的解析与统计：全是纯函数，不碰网络，可离线测试。

输入是已经 `json.loads` 的上游文档；统计口径在每个函数的文档里写清楚。
"""

from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any

from app.providers.errors import ProviderError
from app.utils.market_time import SHANGHAI

Doc = Mapping[str, Any]


def load_json(payload: bytes, label: str) -> Any:
    try:
        return json.loads(payload)
    except ValueError as error:
        raise ProviderError(
            code="INVALID_RESPONSE", message=f"{label}返回了无法解析的内容", status_code=502
        ) from error


def as_doc(value: Any) -> Doc:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def num(value: Any) -> float | None:
    """上游缺值常写成 "-" / "" / None，统一成 None；布尔不当数字。"""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text or text in {"-", "--", "None", "null"}:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def rnd(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def to_yi(value: Any) -> float | None:
    number = num(value)
    return None if number is None else round(number / 1e8, 2)


def to_wan(value: Any) -> float | None:
    number = num(value)
    return None if number is None else round(number / 1e4, 2)


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def suffix_code(code: str) -> str:
    """6 位代码补交易所后缀（与前端 `StockDetailPanel` 的约定一致）。"""

    if code.startswith(("6", "9")) and not code.startswith("92"):
        return f"{code}.SH"
    if code.startswith(("4", "8")) or code.startswith("92"):
        return f"{code}.BJ"
    return f"{code}.SZ"


def secid_for(code: str) -> str:
    clean = code.split(".")[0]
    if clean.startswith("6"):
        return f"1.{clean}"
    return f"0.{clean}"


def clock(value: Any) -> str:
    number = num(value)
    if number is None or number < 0:
        return ""
    raw = f"{int(number):06d}"
    return f"{raw[:2]}:{raw[2:4]}:{raw[4:]}"


# ───────────── 行情 / 分时 / K 线 ─────────────


def parse_ulist(doc: Doc) -> dict[str, Doc]:
    """ulist.np 的 diff → {"市场.代码": 行}，避免上证指数与平安银行同为 000001 时互相覆盖。"""

    rows = as_list(as_doc(doc.get("data")).get("diff"))
    out: dict[str, Doc] = {}
    for row in rows:
        item = as_doc(row)
        code = text(item.get("f12"))
        if code:
            out[f"{text(item.get('f13'))}.{code}"] = item
    return out


@dataclass(frozen=True)
class TrendRow:
    day: str
    clock: str
    price: float
    volume: float
    amount: float
    avg: float


@dataclass(frozen=True)
class Trend:
    name: str
    pre_close: float | None
    rows: tuple[TrendRow, ...]


def parse_trends(doc: Doc) -> Trend:
    """trends2 每行：时间,开,收,高,低,成交量(手),成交额(元),均价。"""

    data = as_doc(doc.get("data"))
    rows: list[TrendRow] = []
    for raw in as_list(data.get("trends")):
        parts = str(raw).split(",")
        if len(parts) < 8 or len(parts[0]) < 16:
            continue
        price, volume, amount, avg = (num(parts[2]), num(parts[5]), num(parts[6]), num(parts[7]))
        if price is None or volume is None or amount is None:
            continue
        rows.append(
            TrendRow(
                day=parts[0][:10],
                clock=parts[0][11:16],
                price=price,
                volume=volume,
                amount=amount,
                avg=avg if avg is not None else price,
            )
        )
    return Trend(name=text(data.get("name")), pre_close=num(data.get("preClose")), rows=tuple(rows))


def latest_day_rows(trend: Trend) -> tuple[TrendRow, ...]:
    if not trend.rows:
        return ()
    last = trend.rows[-1].day
    return tuple(row for row in trend.rows if row.day == last)


def amount_until(rows: Iterable[TrendRow], day: str, limit: str = "11:30") -> float | None:
    """某日截至 `limit`（含）的累计成交额，没有该日分时返回 None。"""

    picked = [row.amount for row in rows if row.day == day and row.clock <= limit]
    return sum(picked) if picked else None


def downsample(values: Sequence[float], size: int) -> list[float]:
    if len(values) <= size:
        return list(values)
    step = (len(values) - 1) / (size - 1)
    return [values[round(i * step)] for i in range(size)]


@dataclass(frozen=True)
class KBar:
    day: str
    open: float
    close: float
    high: float
    low: float
    volume: float
    amount: float
    change_pct: float | None
    turnover: float | None


def parse_klines(doc: Doc) -> list[KBar]:
    """kline 每行：日期,开,收,高,低,成交量,成交额,振幅,涨跌幅,涨跌额,换手率。"""

    bars: list[KBar] = []
    for raw in as_list(as_doc(doc.get("data")).get("klines")):
        parts = str(raw).split(",")
        if len(parts) < 7:
            continue
        o, c, h, low, v, a = (num(p) for p in parts[1:7])
        if None in (o, c, h, low, v, a):
            continue
        assert o is not None and c is not None and h is not None
        assert low is not None and v is not None and a is not None
        bars.append(
            KBar(
                day=parts[0],
                open=o,
                close=c,
                high=h,
                low=low,
                volume=v,
                amount=a,
                change_pct=num(parts[8]) if len(parts) > 8 else None,
                turnover=num(parts[10]) if len(parts) > 10 else None,
            )
        )
    return bars


# ───────────── 涨跌分布 / 中位数 / 极端波动 ─────────────


@dataclass(frozen=True)
class Breadth:
    up: int
    down: int
    flat: int


def breadth_from_buckets(buckets: Mapping[int, int]) -> Breadth:
    """档位 >0 为上涨（11 为涨停），<0 为下跌（-11 为跌停），0 为平盘。"""

    up = sum(count for bucket, count in buckets.items() if bucket > 0)
    down = sum(count for bucket, count in buckets.items() if bucket < 0)
    return Breadth(up=up, down=down, flat=buckets.get(0, 0))


def median_rank_pages(active: int, page_size: int = 100) -> list[tuple[int, int]]:
    """按涨跌幅降序排名，求中位数需要的 (页码, 页内下标)。偶数只数时取中间两位。"""

    if active <= 0:
        return []
    ranks = sorted({(active - 1) // 2, active // 2})
    return [(rank // page_size + 1, rank % page_size) for rank in ranks]


def mean_of(values: Sequence[float | None]) -> float | None:
    picked = [value for value in values if value is not None]
    return None if not picked else round(mean(picked), 2)


def is_new_listing(name: str) -> bool:
    """东财把上市前几天的新股名称加 N / C 前缀，它们不受常规涨跌幅限制。"""

    return name.startswith(("N", "C"))


def is_st(name: str) -> bool:
    return "ST" in name.upper().replace(" ", "")[:4]


def natural_limit_count(names: Iterable[str]) -> int:
    """自然涨停 / 跌停：剔除 ST 与新股。"""

    return sum(1 for name in names if not is_st(name) and not is_new_listing(name))


@dataclass(frozen=True)
class Swing:
    code: str
    name: str
    price: float | None
    change_pct: float
    peak_pct: float
    trough_pct: float
    sector: str


def swing_rows(rows: Iterable[Doc]) -> list[Swing]:
    """从 clist 行取日内最高 / 最低相对昨收的幅度；缺价、停牌、新股一律剔除。"""

    out: list[Swing] = []
    for row in rows:
        name = text(row.get("f14"))
        price, change = num(row.get("f2")), num(row.get("f3"))
        high, low, pre = num(row.get("f15")), num(row.get("f16")), num(row.get("f18"))
        if None in (price, change, high, low, pre) or not pre or is_new_listing(name):
            continue
        assert change is not None and high is not None and low is not None and pre is not None
        out.append(
            Swing(
                code=text(row.get("f12")),
                name=name,
                price=price,
                change_pct=change,
                peak_pct=(high - pre) / pre * 100,
                trough_pct=(low - pre) / pre * 100,
                sector=text(row.get("f100")),
            )
        )
    return out


def pullbacks(swings: Iterable[Swing], threshold: float = 10.0) -> list[Swing]:
    """冲高回落：日内最高涨幅 − 收盘涨幅 ≥ 阈值（单位：个百分点）。"""

    hits = [s for s in swings if s.peak_pct - s.change_pct >= threshold]
    return sorted(hits, key=lambda s: s.peak_pct - s.change_pct, reverse=True)


def rebounds(swings: Iterable[Swing], threshold: float = 10.0) -> list[Swing]:
    """探底回升：收盘涨幅 − 日内最低涨幅 ≥ 阈值。"""

    hits = [s for s in swings if s.change_pct - s.trough_pct >= threshold]
    return sorted(hits, key=lambda s: s.change_pct - s.trough_pct, reverse=True)


# ───────────── 涨停池 / 昨日池 / 连板 ─────────────


@dataclass(frozen=True)
class PoolRow:
    code: str
    name: str
    price: float | None
    change_pct: float | None
    streak: int
    first_seal: str
    last_seal: str
    seal_fund: float | None
    breaks: int
    float_cap: float | None
    turnover: float | None
    industry: str
    amount: float | None = None


def parse_today_pool(doc: Doc) -> list[PoolRow]:
    rows: list[PoolRow] = []
    for raw in as_list(as_doc(doc.get("data")).get("pool")):
        item = as_doc(raw)
        code, name = text(item.get("c")), text(item.get("n"))
        if not code or not name:
            continue
        price = num(item.get("p"))
        rows.append(
            PoolRow(
                code=code,
                name=name,
                price=None if price is None else price / 1000,
                change_pct=rnd(num(item.get("zdp"))),
                streak=int(num(item.get("lbc")) or 1),
                first_seal=clock(item.get("fbt")),
                last_seal=clock(item.get("lbt")),
                seal_fund=num(item.get("fund")),
                breaks=int(num(item.get("zbc")) or 0),
                float_cap=num(item.get("ltsz")),
                turnover=rnd(num(item.get("hs"))),
                industry=text(item.get("hybk")),
                amount=num(item.get("amount")),
            )
        )
    return rows


@dataclass(frozen=True)
class YesterdayRow:
    code: str
    name: str
    price: float | None
    change_pct: float | None
    prev_streak: int
    turnover: float | None
    industry: str


def parse_yesterday_pool(doc: Doc) -> list[YesterdayRow]:
    """昨日涨停池：zdp 是「今日涨跌幅」，ylbc 是昨日连板数。"""

    rows: list[YesterdayRow] = []
    for raw in as_list(as_doc(doc.get("data")).get("pool")):
        item = as_doc(raw)
        code, name = text(item.get("c")), text(item.get("n"))
        if not code or not name:
            continue
        price = num(item.get("p"))
        rows.append(
            YesterdayRow(
                code=code,
                name=name,
                price=None if price is None else price / 1000,
                change_pct=num(item.get("zdp")),
                prev_streak=int(num(item.get("ylbc")) or 1),
                turnover=rnd(num(item.get("hs"))),
                industry=text(item.get("hybk")),
            )
        )
    return rows


def premium(rows: Iterable[YesterdayRow], *, min_streak: int = 1) -> float | None:
    """昨日涨停（或昨日连板）股今日平均涨跌幅，即接力溢价率。"""

    changes = [
        r.change_pct for r in rows if r.prev_streak >= min_streak and r.change_pct is not None
    ]
    return mean_of(changes)


def board_label(streak: int) -> str:
    return "首板" if streak <= 1 else f"{streak}连板"


@dataclass(frozen=True)
class Promotion:
    overall_pct: float | None
    broken_pct: float | None
    r12: float | None
    t12: str
    r23: float | None
    t23: str
    r34: float | None
    t34: str


def _rate(done: int, base: int) -> float | None:
    return None if base == 0 else round(done / base * 100, 2)


def promotion(today: Iterable[PoolRow], yesterday: Sequence[YesterdayRow]) -> Promotion:
    """晋级率口径：昨日 N 板股今日继续涨停（成为 N+1 板）的比例。

    - 1进2：昨日首板 → 今日 ≥2 板；2进3：昨日 2 板 → 今日 ≥3 板；3进4+：昨日 ≥3 板 → 今日再涨停。
    - 整体晋级率 = 昨日涨停今日继续涨停的家数 / 昨日涨停家数；断板率 = 1 − 整体晋级率。
    """

    streak_today = {row.code: row.streak for row in today}
    y1 = [r for r in yesterday if r.prev_streak == 1]
    y2 = [r for r in yesterday if r.prev_streak == 2]
    y3 = [r for r in yesterday if r.prev_streak >= 3]
    up12 = sum(1 for r in y1 if streak_today.get(r.code, 0) >= 2)
    up23 = sum(1 for r in y2 if streak_today.get(r.code, 0) >= 3)
    up34 = sum(1 for r in y3 if streak_today.get(r.code, 0) > r.prev_streak)
    again = sum(1 for r in yesterday if r.code in streak_today)
    overall = _rate(again, len(yesterday))
    return Promotion(
        overall_pct=overall,
        broken_pct=None if overall is None else round(100 - overall, 2),
        r12=_rate(up12, len(y1)),
        t12=f"{up12}/{len(y1)}",
        r23=_rate(up23, len(y2)),
        t23=f"{up23}/{len(y2)}",
        r34=_rate(up34, len(y3)),
        t34=f"{up34}/{len(y3)}",
    )


# ───────────── 同花顺涨停池（涨停原因） ─────────────


@dataclass(frozen=True)
class ThsReason:
    reason: str
    high_days: str
    open_times: int
    order_amount: float | None


def parse_ths_pool(doc: Doc) -> tuple[dict[str, ThsReason], int]:
    """返回 ({代码: 涨停原因}, 总页数)。`order_amount` 为封单金额（元）。"""

    data = as_doc(doc.get("data"))
    page = as_doc(data.get("page"))
    pages = int(num(page.get("count")) or 1)
    out: dict[str, ThsReason] = {}
    for raw in as_list(data.get("info")):
        item = as_doc(raw)
        code = text(item.get("code"))
        if code:
            out[code] = ThsReason(
                reason=text(item.get("reason_type")),
                high_days=text(item.get("high_days")),
                open_times=int(num(item.get("open_num")) or 0),
                order_amount=num(item.get("order_amount")),
            )
    return out, pages


@dataclass(frozen=True)
class ThsBlock:
    code: str
    name: str
    change_pct: float | None
    limit_up_num: int
    leader_code: str
    leader_name: str
    leader_change: float | None
    leader_label: str


_PSEUDO_BOARD = re.compile(
    r"昨日|连板|涨停|炸板|打板|含一字|ST|百元|新股|次新|融资融券|转债|MSCI|标准普尔|富时|沪股通|深股通"
)


def parse_ths_blocks(doc: Doc) -> list[ThsBlock]:
    out: list[ThsBlock] = []
    for raw in as_list(doc.get("data")):
        item = as_doc(raw)
        name = text(item.get("name"))
        if not name or _PSEUDO_BOARD.search(name):
            continue
        stocks = [as_doc(s) for s in as_list(item.get("stock_list"))]
        leader = max(stocks, key=lambda s: num(s.get("continue_num")) or 0, default={})
        out.append(
            ThsBlock(
                code=text(item.get("code")),
                name=name,
                change_pct=rnd(num(item.get("change"))),
                limit_up_num=int(num(item.get("limit_up_num")) or 0),
                leader_code=text(leader.get("code")),
                leader_name=text(leader.get("name")),
                leader_change=rnd(num(leader.get("change_rate"))),
                leader_label=text(leader.get("high")),
            )
        )
    return out


# ───────────── 两融 ─────────────


@dataclass(frozen=True)
class MarginDay:
    day: str
    values: Mapping[str, float | None]


def parse_margin(doc: Doc) -> list[MarginDay]:
    """两融历史，按日期升序。前缀：TOTAL_ 全市场，H_ 沪，S_ 深，B_ 京。"""

    rows = as_list(as_doc(doc.get("result")).get("data"))
    days: list[MarginDay] = []
    for raw in rows:
        item = as_doc(raw)
        day = text(item.get("DIM_DATE"))[:10]
        if day:
            days.append(MarginDay(day=day, values={k: num(v) for k, v in item.items()}))
    return sorted(days, key=lambda d: d.day)


# ───────────── 期指席位 ─────────────

KEY_MEMBERS = ("中信期货", "国泰君安", "海通期货", "国泰海通", "国信期货")
_PRODUCT = re.compile(r"^(IF|IC|IM|IH)\d{4}$")
_DISPLAY = {"国泰君安": "国泰海通(国泰君安)", "海通期货": "国泰海通(海通期货)"}


@dataclass
class FuturesAgg:
    long_pos: int = 0
    long_change: int = 0
    short_pos: int = 0
    short_change: int = 0


def display_member(name: str) -> str:
    base = re.sub(r"[（(](代客|自营)[）)]$", "", name.strip())
    return _DISPLAY.get(base, base)


def is_key_member(name: str) -> bool:
    return any(name.startswith(key) for key in KEY_MEMBERS)


def aggregate_futures(rows: Iterable[Doc]) -> dict[tuple[str, str], FuturesAgg]:
    """把各月份合约的席位持仓按「品种 × 席位」累加。

    中金所每个合约只公布持买单量前 20 名的席位，所以这里是「榜内席位」口径，
    不是品种全市场持仓；排名外的席位不会出现。
    """

    out: dict[tuple[str, str], FuturesAgg] = defaultdict(FuturesAgg)
    for row in rows:
        contract = text(row.get("SECURITY_CODE"))
        if not _PRODUCT.match(contract):
            continue
        member = display_member(text(row.get("MEMBER_NAME_ABBR")))
        if not member or member == "本日合计":
            continue
        agg = out[(contract[:2], member)]
        agg.long_pos += int(num(row.get("LONG_POSITION")) or 0)
        agg.long_change += int(num(row.get("LP_CHANGE")) or 0)
        agg.short_pos += int(num(row.get("SHORT_POSITION")) or 0)
        agg.short_change += int(num(row.get("SP_CHANGE")) or 0)
    return dict(out)


# ───────────── 资讯：分级 / 情绪 / 去噪 ─────────────

_S_WORDS = (
    "国务院",
    "中央",
    "央行",
    "证监会",
    "发改委",
    "财政部",
    "工信部",
    "降准",
    "降息",
    "加息",
    "美联储",
    "鲍威尔",
    "关税",
    "制裁",
    "重组",
    "停牌",
    "立案",
    "退市",
    "突发",
    "暴跌",
    "暴涨",
    "熔断",
    "战争",
    "加征",
    "特朗普",
    "国家队",
    "汇金",
)
_A_WORDS = (
    "业绩预增",
    "预增",
    "预亏",
    "中标",
    "签订",
    "订单",
    "获批",
    "批复",
    "增持",
    "回购",
    "减持",
    "涨停",
    "大涨",
    "大跌",
    "财报",
    "并购",
    "收购",
    "战略合作",
)
_BULL = (
    "增长",
    "预增",
    "上调",
    "突破",
    "中标",
    "订单",
    "获批",
    "批复",
    "回购",
    "增持",
    "涨停",
    "大涨",
    "利好",
    "补贴",
    "支持",
    "签约",
    "扭亏",
    "新高",
    "超预期",
    "放量",
    "涨价",
    "提价",
    "降准",
    "降息",
)
_BEAR = (
    "下跌",
    "暴跌",
    "下调",
    "减持",
    "处罚",
    "立案",
    "亏损",
    "预亏",
    "退市",
    "风险",
    "终止",
    "违约",
    "制裁",
    "加征",
    "收紧",
    "大跌",
    "跳水",
    "新低",
    "不及预期",
    "下滑",
    "召回",
    "问询",
    "警示",
    "爆雷",
)
_CATALYST = (
    "发布会",
    "大会",
    "传闻",
    "拟",
    "有望",
    "预期",
    "试点",
    "筹划",
    "重组",
    "合作",
    "计划",
    "启动",
    "推出",
    "落地",
    "征求意见",
    "调研",
    "论坛",
    "展会",
    "峰会",
)
_OVERSEAS = (
    "美联储",
    "美股",
    "纳指",
    "标普",
    "道指",
    "欧央行",
    "日央行",
    "原油",
    "黄金",
    "美元",
    "美债",
    "欧盟",
    "台股",
    "日经",
    "亚洲股市",
    "欧洲股市",
    "英国",
    "日本",
    "俄罗斯",
    "乌克兰",
    "以色列",
    "中东",
    "特朗普",
    "非农",
    "OPEC",
)


def _hits(content: str, words: Iterable[str]) -> int:
    return sum(1 for word in words if word in content)


def classify_sentiment(content: str) -> str:
    """关键词规则判定，不是模型判断：利好词多于利空词为「利好」，反之「利空」，
    都没有但命中催化词为「催化」，否则「中性」。"""

    bull, bear = _hits(content, _BULL), _hits(content, _BEAR)
    if bull > bear:
        return "利好"
    if bear > bull:
        return "利空"
    return "催化" if _hits(content, _CATALYST) else "中性"


def classify_impact(
    content: str, *, source_hint: str | None = None, has_stocks: bool = False
) -> str:
    """S / A / B 分级：数据源自带的重要性标记优先（财联社 A 级 / 金十 important），否则按关键词。"""

    if source_hint in {"S", "A"}:
        return source_hint
    if _hits(content, _S_WORDS) >= 1:
        return "S"
    if _hits(content, _A_WORDS) >= 1 or has_stocks:
        return "A"
    return "B"


def is_overseas(content: str) -> bool:
    return _hits(content, _OVERSEAS) >= 1


_SECTOR_NOISE = {
    "盘面直播",
    "港股动态",
    "美股动态",
    "要闻",
    "A股公告",
    "公司公告",
    "财联社早知道",
    "市场情绪",
}


_A_SHARE_STOCK = re.compile(
    r"^(00[0-3]|30[01]|60[0-5]|68[89]|4\d\d|8\d\d|92\d)\d{3}(\.(SH|SZ|BJ))?$"
)
_HTML_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG = re.compile(r"<[^>]+>")


def is_a_share_stock(code: str) -> bool:
    """只认 A 股个股代码；基金/ETF/LOF（15/16/5 开头）等不当作「关联个股」。"""

    return _A_SHARE_STOCK.match(code) is not None


def strip_html(raw: str) -> str:
    """金十等来源的正文带 `<br/>`、`<span>` 之类的标记，展示前统一清掉。"""

    text = html.unescape(_HTML_TAG.sub("", _HTML_BREAK.sub(" ", raw)))
    return re.sub(r"\s+", " ", text).strip()


def clean_sectors(names: Iterable[str]) -> list[str]:
    seen: list[str] = []
    for name in names:
        label = name.strip()
        if label and label not in _SECTOR_NOISE and label not in seen:
            seen.append(label)
    return seen[:4]


def day_clock(timestamp: int) -> tuple[str, str]:
    moment = datetime.fromtimestamp(timestamp, tz=SHANGHAI)
    return moment.strftime("%Y-%m-%d"), moment.strftime("%H:%M")
