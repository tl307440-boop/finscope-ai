from datetime import date

import pytest
from app.services.assistant.fast_path import PlannedCall, plan
from app.services.assistant.results import Column, ToolResult, unavailable
from app.services.assistant.tool_cache import ToolCache, cache_key


def _series_codes(question: str) -> list[str]:
    return [call.args["code"] for call in plan(question) if call.name == "macro_series"]


@pytest.mark.parametrize(
    ("question", "codes"),
    [
        ("美国CPI最近怎么走", ["CPIAUCSL"]),
        ("CPI 最近怎么样", ["CPIAUCSL"]),
        ("中国CPI最近怎么走", ["CPI_CN"]),
        ("国内 CPI 和 PPI 同比", ["CPI_CN", "PPI_CN"]),
        ("美国10年期国债收益率现在多少", ["DGS10"]),
        ("2年期与10年期美债收益率", ["DGS10", "DGS2"]),
        ("美国失业率和非农", ["UNRATE", "PAYEMS"]),
        ("中国PMI多少", ["PMI_CN"]),
        ("美国PMI多少", ["ISM_MFG"]),
        ("M2 增速", ["M2_CN"]),
    ],
)
def test_clear_questions_prefetch_the_right_series(question: str, codes: list[str]) -> None:
    assert sorted(_series_codes(question)) == sorted(codes)


@pytest.mark.parametrize(
    "question",
    [
        "PMI 怎么样",  # 没说中美，不猜
        "中国失业率多少",  # 库里没有中国失业率，不能拿美国的顶
        "核心CPI 怎么样",  # 目录里没有美国核心 CPI
        "利率最近怎么样",
        "帮我分析一下大盘",
        "",
    ],
)
def test_ambiguous_or_unknown_questions_do_not_prefetch(question: str) -> None:
    assert plan(question) == []


def test_more_than_three_series_is_treated_as_ambiguous() -> None:
    assert _series_codes("美国CPI 失业率 非农 联邦基金利率 10年期国债") == []


def test_custom_indicators_need_an_unambiguous_group() -> None:
    assert plan("沪深300 PCR 现在多少") == [
        PlannedCall("custom_indicator", {"indicator": "pcr", "group": "csi300"})
    ]
    assert plan("中证1000期权认沽认购比") == [
        PlannedCall("custom_indicator", {"indicator": "pcr", "group": "csi1000"})
    ]
    assert plan("PCR 现在多少") == []  # 没说哪个指数
    assert plan("沪深300 和 上证50 的 PCR") == []  # 对上两个，不猜
    assert plan("股债性价比现在怎么样") == [
        PlannedCall("custom_indicator", {"indicator": "valuation"})
    ]
    both = plan("巴菲特指标和股债性价比")
    assert both == [PlannedCall("custom_indicator", {"indicator": "valuation"})]


def test_six_digit_code_searches_the_instrument_only_when_nothing_else_matches() -> None:
    assert plan("600519 最近一年涨了多少") == [
        PlannedCall("instrument_search", {"query": "600519"})
    ]
    assert plan("美国CPI 和 600519") == [PlannedCall("macro_series", {"code": "CPIAUCSL"})]
    assert len(plan("600519 对比 000858 和 000001")) == 2


def _result(tool: str = "macro_series", status: str = "ok") -> ToolResult:
    return ToolResult(
        tool=tool,
        title="t",
        columns=(Column("v", "v", "", "number"),),
        rows=({"v": "1"},),
        as_of=date(2026, 10, 17),
        source="s",
        status=status,  # type: ignore[arg-type]
    )


def test_cache_hits_until_ttl_and_evicts_least_recently_used() -> None:
    now = [0.0]
    cache = ToolCache(max_entries=2, ttl_seconds=300, clock=lambda: now[0])
    key_a = cache_key("macro_series", {"code": "A"}, date(2026, 10, 17))
    key_b = cache_key("macro_series", {"code": "B"}, date(2026, 10, 17))
    key_c = cache_key("macro_series", {"code": "C"}, date(2026, 10, 17))
    cache.put(key_a, _result())
    cache.put(key_b, _result())
    assert cache.get(key_a) is not None  # a 变成最近使用
    cache.put(key_c, _result())  # 挤掉 b
    assert cache.get(key_b) is None and cache.get(key_a) is not None
    now[0] = 299.9
    assert cache.get(key_a) is not None
    now[0] = 301.0
    assert cache.get(key_a) is None
    assert len(cache) == 1  # 过期的 a 读到时被清掉，c 还在表里


def test_watchlist_and_unavailable_results_are_never_cached() -> None:
    cache = ToolCache()
    key = cache_key("watchlist", {}, date(2026, 10, 17))
    cache.put(key, _result("watchlist"))
    assert cache.get(key) is None
    gone = unavailable("macro_series", "t", date(2026, 10, 17), "没有数据")
    key2 = cache_key("macro_series", {"code": "A"}, date(2026, 10, 17))
    cache.put(key2, gone)
    assert cache.get(key2) is None


def test_cache_key_ignores_argument_order_but_not_values_or_date() -> None:
    day = date(2026, 10, 17)
    assert cache_key("t", {"a": 1, "b": 2}, day) == cache_key("t", {"b": 2, "a": 1}, day)
    assert cache_key("t", {"a": 1}, day) != cache_key("t", {"a": 2}, day)
    assert cache_key("t", {"a": 1}, day) != cache_key("t", {"a": 1}, date(2026, 10, 18))
