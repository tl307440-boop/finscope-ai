"""A 股全景看板：统计口径与异常路径的离线测试（样本形状取自 2026-11 实测的上游返回）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime

import pytest
from app.providers import ashare_panorama as src
from app.providers.errors import ProviderError
from app.services import ashare_calc as calc
from app.services.ashare_panorama import ASharePanoramaService
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI


def test_cls_sign_is_md5_of_sha1() -> None:
    expected = hashlib.md5(hashlib.sha1(b"app=x").hexdigest().encode()).hexdigest()
    assert src.cls_sign("app=x") == expected
    url = src.cls_telegraph_url(5, now=1_700_000_000)
    assert "sign=" in url and "last_time=1700000000" in url
    query, _, sign = url.split("?", 1)[1].rpartition("&sign=")
    assert sign == src.cls_sign(query)
    # 参数必须按 key 排序，否则签名对不上
    keys = [part.split("=")[0] for part in query.split("&")]
    assert keys == sorted(keys)


def test_breadth_and_median_pages() -> None:
    buckets = {11: 31, 3: 100, 0: 79, -1: 4000, -11: 2}
    breadth = calc.breadth_from_buckets(buckets)
    assert (breadth.up, breadth.down, breadth.flat) == (131, 4002, 79)
    # 偶数只取中间两位，且落在同一页内
    assert calc.median_rank_pages(5300) == [(27, 49), (27, 50)]
    # 奇数只取一位；跨页边界时给出两页
    assert calc.median_rank_pages(5301) == [(27, 50)]
    assert calc.median_rank_pages(201) == [(2, 0)]
    assert calc.median_rank_pages(0) == []


def test_mean_of_ignores_missing() -> None:
    assert calc.mean_of([1.0, None, 2.0]) == 1.5
    assert calc.mean_of([None]) is None


def test_num_handles_dash_and_bool() -> None:
    assert calc.num("-") is None
    assert calc.num(True) is None
    assert calc.num("1,234.5") == 1234.5
    assert calc.num(None) is None


def _row(
    code: str, name: str, price: float, change: float, high: float, low: float, pre: float
) -> dict[str, object]:
    return {
        "f12": code,
        "f14": name,
        "f2": price,
        "f3": change,
        "f15": high,
        "f16": low,
        "f18": pre,
        "f100": "行业",
    }


def test_swing_thresholds_and_exclusions() -> None:
    pre = 10.0
    rows = [
        _row("1", "冲高回落", 9.0, -10.0, 11.5, 8.9, pre),  # 峰 +15% → 收 -10%：回落 25 点
        _row("2", "探底回升", 10.5, 5.0, 10.6, 8.4, pre),  # 谷 -16% → 收 +5%：回升 21 点
        _row("3", "普通", 10.2, 2.0, 10.5, 9.8, pre),
        _row("4", "N新股", 20.0, 100.0, 30.0, 10.0, pre),  # 新股剔除
        _row("5", "停牌", 0, 0, 0, 0, 0),  # 缺昨收剔除
    ]
    swings = calc.swing_rows(rows)
    assert [s.name for s in swings] == ["冲高回落", "探底回升", "普通"]
    assert [s.name for s in calc.pullbacks(swings)] == ["冲高回落"]
    assert [s.name for s in calc.rebounds(swings)] == ["探底回升"]


def test_natural_limit_excludes_st_and_new() -> None:
    assert calc.natural_limit_count(["平安银行", "*ST 某某", "ST高鸿", "N森峰", "C创达"]) == 1


def _pool(code: str, streak: int) -> calc.PoolRow:
    return calc.PoolRow(
        code=code,
        name=code,
        price=1.0,
        change_pct=10.0,
        streak=streak,
        first_seal="09:30:00",
        last_seal="09:30:00",
        seal_fund=1e8,
        breaks=0,
        float_cap=1e9,
        turnover=5.0,
        industry="x",
    )


def _y(code: str, prev: int, change: float) -> calc.YesterdayRow:
    return calc.YesterdayRow(
        code=code,
        name=code,
        price=1.0,
        change_pct=change,
        prev_streak=prev,
        turnover=1.0,
        industry="x",
    )


def test_promotion_rates_by_tier() -> None:
    yesterday = [
        _y("a", 1, 10),
        _y("b", 1, -3),
        _y("c", 1, 10),
        _y("d", 2, 10),
        _y("e", 2, -5),
        _y("f", 3, 10),
    ]
    today = [_pool("a", 2), _pool("c", 2), _pool("d", 3), _pool("f", 4), _pool("z", 1)]
    promo = calc.promotion(today, yesterday)
    assert (promo.t12, promo.r12) == ("2/3", 66.67)
    assert (promo.t23, promo.r23) == ("1/2", 50.0)
    assert (promo.t34, promo.r34) == ("1/1", 100.0)
    assert promo.overall_pct == 66.67  # 4 / 6
    assert promo.broken_pct == 33.33


def test_promotion_without_yesterday_is_unknown_not_zero() -> None:
    promo = calc.promotion([_pool("a", 2)], [])
    assert promo.overall_pct is None and promo.r12 is None


def test_premium_streak_filter() -> None:
    rows = [_y("a", 1, 4.0), _y("b", 2, 8.0), _y("c", 3, 6.0)]
    assert calc.premium(rows) == 6.0
    assert calc.premium(rows, min_streak=2) == 7.0
    assert calc.premium([]) is None


def test_trends_parse_and_noon_amount() -> None:
    doc = {
        "data": {
            "name": "上证指数",
            "preClose": 100.0,
            "trends": [
                "2026-11-05 11:30,1,1,1,1,10,100.0,1",
                "2026-11-05 13:00,1,1,1,1,10,50.0,1",
                "2026-11-06 09:30,1,1,1,1,10,70.0,1",
                "2026-11-06 11:30,1,1,1,1,10,30.0,1",
                "2026-11-06 11:31,1,1,1,1,10,999.0,1",
                "bad",
            ],
        }
    }
    trend = calc.parse_trends(doc)
    assert len(trend.rows) == 5
    assert calc.amount_until(trend.rows, "2026-11-06") == 100.0
    assert calc.amount_until(trend.rows, "2026-11-05") == 100.0
    assert calc.amount_until(trend.rows, "2026-11-04") is None
    assert [r.clock for r in calc.latest_day_rows(trend)] == ["09:30", "11:30", "11:31"]


def test_futures_aggregate_sums_contract_months_and_merges_names() -> None:
    def row(code: str, member: str, lp: int, lc: int, sp: int, sc: int) -> dict[str, object]:
        return {
            "SECURITY_CODE": code,
            "MEMBER_NAME_ABBR": member,
            "LONG_POSITION": lp,
            "LP_CHANGE": lc,
            "SHORT_POSITION": sp,
            "SP_CHANGE": sc,
        }

    rows = [
        row("IC2611", "中信期货(代客)", 100, 10, 200, -20),
        row("IC2612", "中信期货(代客)", 50, -5, 60, 5),
        row("IC2612", "国泰君安(代客)", 70, 1, 10, 2),
        row("IC2612", "本日合计", 999, 0, 999, 0),
        row("T2612", "中信期货(代客)", 1, 1, 1, 1),  # 国债期货不计入
    ]
    agg = calc.aggregate_futures(rows)
    citic = agg[("IC", "中信期货")]
    assert (citic.long_pos, citic.long_change, citic.short_pos, citic.short_change) == (
        150,
        5,
        260,
        -15,
    )
    assert ("IC", "国泰海通(国泰君安)") in agg
    assert not any(member == "本日合计" for _, member in agg)
    assert calc.is_key_member("国泰海通(海通期货)") and not calc.is_key_member("银河期货")


def test_news_classification_rules() -> None:
    assert calc.classify_sentiment("公司预增 订单大增 获批") == "利好"
    assert calc.classify_sentiment("公司被立案 业绩预亏 风险提示") == "利空"
    assert calc.classify_sentiment("公司将召开发布会") == "催化"
    assert calc.classify_sentiment("今日天气晴") == "中性"
    assert calc.classify_impact("央行宣布降准") == "S"
    assert calc.classify_impact("普通消息", source_hint="S") == "S"
    assert calc.classify_impact("某公司中标项目") == "A"
    assert calc.classify_impact("今日天气晴") == "B"
    assert calc.is_overseas("美联储官员发表讲话") and not calc.is_overseas("国内消费数据")


def test_news_strips_html_and_drops_fund_codes() -> None:
    raw = '<b>标题</b><br/>1.内容<span class="x">甲</span>&amp;乙'
    assert calc.strip_html(raw) == "标题 1.内容甲&乙"
    assert calc.is_a_share_stock("002578.SZ") and calc.is_a_share_stock("688001")
    assert calc.is_a_share_stock("830799.BJ")
    # 人工智能 LOF / 沪市 ETF 不是个股
    assert not calc.is_a_share_stock("161631.SZ") and not calc.is_a_share_stock("510300.SH")

    from app.services.ashare_panorama import _jin10_items

    items = _jin10_items(
        [
            {
                "id": "1",
                "time": "2026-11-06 14:10:00",
                "important": 1,
                "data": {"content": "<b>要闻</b><br/>内容<span>很长</span>"},
            }
        ]
    )
    assert "<" not in items[0].summary and "<" not in items[0].title


def test_ths_blocks_filter_pseudo_boards() -> None:
    doc = {
        "data": [
            {"code": "1", "name": "昨日连板", "change": 3, "limit_up_num": 9, "stock_list": []},
            {
                "code": "2",
                "name": "机器人",
                "change": 2.5,
                "limit_up_num": 5,
                "stock_list": [
                    {
                        "code": "000001",
                        "name": "甲",
                        "continue_num": 1,
                        "change_rate": 10,
                        "high": "首板",
                    },
                    {
                        "code": "000002",
                        "name": "乙",
                        "continue_num": 3,
                        "change_rate": 9.9,
                        "high": "3天3板",
                    },
                ],
            },
        ]
    }
    blocks = calc.parse_ths_blocks(doc)
    assert [b.name for b in blocks] == ["机器人"]
    assert blocks[0].leader_name == "乙" and blocks[0].leader_label == "3天3板"


def test_suffix_and_secid() -> None:
    assert calc.suffix_code("600000") == "600000.SH"
    assert calc.suffix_code("688001") == "688001.SH"
    assert calc.suffix_code("000001") == "000001.SZ"
    assert calc.suffix_code("300750") == "300750.SZ"
    assert calc.suffix_code("920007") == "920007.BJ"
    assert calc.suffix_code("830799") == "830799.BJ"
    assert calc.secid_for("600000") == "1.600000"
    assert calc.secid_for("000001.SZ") == "0.000001"


# ───────────── 服务层：上游失败时不造假 ─────────────


class _Gateway:
    def __init__(self, routes: Mapping[str, object], fail: tuple[str, ...] = ()) -> None:
        self.routes = routes
        self.fail = fail
        self.calls: list[str] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        for marker in self.fail:
            if marker in url:
                raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)
        for marker, body in self.routes.items():
            if marker in url:
                return json.dumps(body).encode()
        raise ProviderError(code="NO_ROUTE", message=f"未配置 {url}", status_code=500)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        return await self.get(url, headers)


def _service(gateway: _Gateway) -> ASharePanoramaService:
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(gateway=gateway, cache=TtlCache(), now=lambda: now)


def test_ladder_reports_unavailable_instead_of_zero_when_pools_fail() -> None:
    kline = {"data": {"klines": ["2026-11-05,1,1,1,1,1,1", "2026-11-06,1,1,1,1,1,1"]}}
    gateway = _Gateway(
        {"kline/get": kline}, fail=("getTopicZTPool", "getYesterdayZTPool", "limit_up_pool")
    )
    payload = asyncio.run(_service(gateway).ladder())
    assert payload.tradeDate == "2026-11-06"
    assert payload.overallPromotionRatePct is None
    assert payload.tiers == [] and payload.brokenStreaks == []
    assert "涨停池" in payload.meta.unavailable
    assert payload.meta.session == "close"


def test_stock_detail_rejects_bad_code() -> None:
    with pytest.raises(ProviderError) as info:
        asyncio.run(_service(_Gateway({})).stock_detail("12ab"))
    assert info.value.status_code == 422


def test_intraday_pullbacks_uses_eastmoney_or_falls_back_to_tencent() -> None:
    kline = {"data": {"klines": ["2026-11-05,1,1,1,1,1,1", "2026-11-06,1,1,1,1,1,1"]}}
    clist = {
        "data": {
            "diff": [
                _row("002045", "国光电器", 9.86, -0.40, 10.89, 9.80, 9.90),
                _row("000001", "平安银行", 10.10, 1.00, 10.20, 9.95, 10.00),
            ]
        }
    }
    gateway = _Gateway({"kline/get": kline, "clist/get": clist}, fail=("web.ifzq.gtimg.cn",))
    res = asyncio.run(_service(gateway).intraday_pullbacks(10.0))
    assert str(res.trade_date) == "2026-11-06"
    assert [s.name for s in res.hits] == ["国光电器"]
    assert res.error is None
