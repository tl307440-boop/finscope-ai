"""市场情绪工具 `market_sentiment`：涨停与连板、涨停股清单、机构调研、热榜；时点与缺数据。"""

from datetime import date
from decimal import Decimal
from typing import Any, cast

from app.providers.errors import ProviderError
from app.schemas.industry_market import HotList, HotPlateItem, HotStockItem, Section
from app.schemas.market_sentiment import (
    CountItem,
    LadderStep,
    LimitUpView,
    PoolStockItem,
    SurveyStockItem,
    SurveyView,
)
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools import sentiment as sentiment_tools
from app.services.industry_market import IndustryMarketService
from app.services.market_sentiment import MarketSentimentService

from live_fakes import NOW, context, upstream_down

OK = Section(status="ok", fetched_at=NOW)
DAY = date(2026, 10, 21)


def _stock(code: str, name: str, streak: int, **extra: Any) -> PoolStockItem:
    return PoolStockItem(code=code, name=name, streak=streak, **extra)


def _limit_up(**changes: Any) -> LimitUpView:
    fields: dict[str, Any] = {
        "section": OK,
        "trade_date": DAY,
        "limit_up": 58,
        "broken": 21,
        "limit_down": 3,
        "seal_rate": Decimal("73.4"),
        "max_streak": 5,
        "ladder": [
            LadderStep(
                streak=5, stocks=[_stock("000001", "甲股份", 5), _stock("000002", "乙股份", 5)]
            ),
            LadderStep(streak=2, stocks=[_stock(f"60000{n}", f"丙{n}", 2) for n in range(8)]),
            LadderStep(streak=1, stocks=[_stock("300001", "首板股", 1)]),
        ],
        "industries": [CountItem(label="半导体", count=9), CountItem(label="软件开发", count=6)],
        "seal_times": [],
        "limit_up_stocks": [
            _stock(
                "000002",
                "乙股份",
                5,
                industry="半导体",
                first_seal="09:31:05",
                change_pct=Decimal("10.01"),
                amount=Decimal("1230000000"),
                seal_fund=Decimal("456000000"),
                breaks=1,
            ),
            _stock("300001", "首板股", 1, first_seal="09:45:00", industry="软件开发"),
            _stock("000001", "甲股份", 5, first_seal="09:30:12", industry="半导体"),
            _stock("600001", "二连板", 2, first_seal="10:01:00"),
        ],
        "broken_stocks": [],
        "limit_down_stocks": [],
        "rules": [],
        "sources": [],
    }
    fields.update(changes)
    return LimitUpView(**fields)


def _surveys(**changes: Any) -> SurveyView:
    fields: dict[str, Any] = {
        "section": OK,
        "days": 30,
        "since": date(2026, 9, 21),
        "events": 400,
        "stocks": 120,
        "institutions": 900,
        "daily": [],
        "ways": [],
        "industries": [],
        "industry_section": OK,
        "top_stocks": [
            SurveyStockItem(
                code="002371",
                name="北方华创",
                events=7,
                institutions=210,
                latest=date(2026, 10, 19),
                industry="半导体",
                ways=["现场参观", "电话会议", "线上交流", "策略会"],
            ),
            SurveyStockItem(
                code="688012",
                name="中微公司",
                events=5,
                institutions=150,
                latest=date(2026, 10, 15),
                ways=[],
            ),
        ],
        "recent": [],
        "rules": [],
        "sources": [],
    }
    fields.update(changes)
    return SurveyView(**fields)


def _hot(**changes: Any) -> HotList:
    stocks = [
        HotStockItem(rank=n, code=f"60000{n}", name=f"热股{n}", tags=["算力", "芯片"])
        for n in range(1, 13)
    ]
    fields: dict[str, Any] = {
        "eastmoney": stocks,
        "eastmoney_section": OK,
        "ths_stocks": [HotStockItem(rank=1, code="300750", name="宁德时代", tags=[], note="龙头")],
        "ths_section": OK,
        "ths_industries": [HotPlateItem(rank=1, code="881121", name="半导体", tag="持续热门")],
        "ths_concepts": [HotPlateItem(rank=1, code="885001", name="人工智能")],
        "plates_section": OK,
        "sources": [],
    }
    fields.update(changes)
    return HotList(**fields)


class FakeSentiment:
    def __init__(self, limit_up: Any = None, surveys: Any = None) -> None:
        self._limit_up, self._surveys = limit_up, surveys
        self.days: list[date | None] = []
        self.survey_days: list[int] = []

    async def limit_up(self, day: date | None = None) -> LimitUpView:
        self.days.append(day)
        if isinstance(self._limit_up, Exception):
            raise self._limit_up
        return cast(LimitUpView, self._limit_up)

    async def surveys(self, days: int) -> SurveyView:
        self.survey_days.append(days)
        if isinstance(self._surveys, Exception):
            raise self._surveys
        return cast(SurveyView, self._surveys)


class FakeMarket:
    def __init__(self, hot: Any) -> None:
        self._hot = hot
        self.calls = 0

    async def hot(self) -> HotList:
        self.calls += 1
        if isinstance(self._hot, Exception):
            raise self._hot
        return cast(HotList, self._hot)


def _run(arguments: str, sentiment: FakeSentiment | None = None, hot: Any = None) -> ToolResult:
    tool = sentiment_tools.MarketSentimentTool(
        cast(MarketSentimentService, sentiment or FakeSentiment()),
        cast(IndustryMarketService, FakeMarket(hot)),
    )
    return ToolRegistry([tool]).execute("market_sentiment", arguments, context()).result


def _items(result: ToolResult) -> dict[str, dict[str, Any]]:
    return {str(row["item"]): row for row in result.rows}


def test_limit_up_summary_has_counts_seal_rate_industries_and_the_ladder() -> None:
    result = _run('{"view": "limit_up"}', FakeSentiment(limit_up=_limit_up()))
    assert result.status == "ok" and result.as_of == DAY and "2026-10-21" in result.title
    rows = _items(result)
    assert (rows["涨停家数"]["value"], rows["炸板家数"]["value"], rows["跌停家数"]["value"]) == (
        "58",
        "21",
        "3",
    )
    assert (rows["封板率"]["value"], rows["封板率"]["unit"]) == ("73.4", "%")
    assert rows["最高连板数"]["value"] == "5"
    assert rows["涨停行业：半导体"]["value"] == "9"
    assert rows["5连板"]["note"] == "甲股份、乙股份"
    assert rows["2连板"]["value"] == "8" and "等 8 只" in str(rows["2连板"]["note"])
    assert "1连板" not in rows  # 首板不进梯队


def test_a_missing_count_stays_missing_and_says_so() -> None:
    view = _limit_up(broken=None, limit_down=None, seal_rate=None)
    rows = _items(_run('{"view": "limit_up"}', FakeSentiment(limit_up=view)))
    assert rows["炸板家数"]["value"] is None and rows["炸板家数"]["note"] == "暂不可用"
    assert rows["跌停家数"]["value"] is None and rows["封板率"]["value"] is None


def test_a_past_trade_date_is_passed_to_the_service_and_none_means_latest() -> None:
    service = FakeSentiment(limit_up=_limit_up(trade_date=date(2026, 10, 15)))
    _run('{"view": "limit_up", "as_of": "2026-10-15"}', service)
    _run('{"view": "limit_up"}', service)
    assert service.days == [date(2026, 10, 15), None]


def test_a_non_trading_day_or_no_pool_is_unavailable_with_the_reason() -> None:
    view = _limit_up(
        trade_date=None,
        limit_up_stocks=[],
        section=Section(status="unavailable", reason="东财涨停池暂时取不到"),
    )
    result = _run('{"view": "limit_up"}', FakeSentiment(limit_up=view))
    assert result.status == "unavailable" and result.reason == "东财涨停池暂时取不到"


def test_stale_pool_is_marked_stale_with_the_reason() -> None:
    stale = Section(status="stale", reason="东财实时池取不到，展示上次抓取的数据")
    result = _run('{"view": "limit_up"}', FakeSentiment(limit_up=_limit_up(section=stale)))
    assert result.status == "stale" and "上次抓取" in (result.reason or "")


def test_limit_up_stocks_are_ordered_by_streak_then_seal_time_and_truncated() -> None:
    service = FakeSentiment(limit_up=_limit_up())
    result = _run('{"view": "limit_up_stocks", "top": 5}', service)
    assert [row["name"] for row in result.rows] == ["甲股份", "乙股份", "二连板", "首板股"]
    assert result.truncated == 0
    second = result.rows[1]
    assert (second["amount"], second["seal_fund"], second["first_seal"]) == (
        "12.3",
        "4.56",
        "09:31:05",
    )
    many = _limit_up(limit_up_stocks=[_stock(f"60{n:04d}", f"票{n}", 1) for n in range(9)])
    cut = _run('{"view": "limit_up_stocks", "top": 5}', FakeSentiment(limit_up=many))
    assert len(cut.rows) == 5 and cut.truncated == 4


def test_no_limit_up_stocks_is_unavailable() -> None:
    view = _limit_up(limit_up_stocks=[])
    result = _run('{"view": "limit_up_stocks"}', FakeSentiment(limit_up=view))
    assert result.status == "unavailable" and "没有涨停股" in (result.reason or "")


YI = Decimal(100_000_000)


def _capped(code: str, name: str, streak: int, total: str | None, float_: str | None) -> Any:
    return _stock(
        code,
        name,
        streak,
        total_cap=None if total is None else Decimal(total) * YI,
        float_cap=None if float_ is None else Decimal(float_) * YI,
    )


def _cap_pool() -> LimitUpView:
    return _limit_up(
        limit_up_stocks=[
            _capped("002724", "海洋王", 5, "86.48", "63.5"),
            _capped("603278", "大业股份", 5, "45", "20"),
            _capped("300001", "创业股", 2, "50", "30"),
            _capped("688001", "科创股", 1, "40", None),
            _capped("000999", "大盘股", 1, "500", "200"),
            _capped("600888", "无市值", 1, None, None),
        ]
    )


def test_limit_up_stocks_show_both_market_caps_in_yi_and_keep_a_plain_title() -> None:
    result = _run('{"view": "limit_up_stocks"}', FakeSentiment(limit_up=_cap_pool()))
    first = result.rows[0]
    assert (first["name"], first["total_cap"], first["float_cap"]) == ("海洋王", "86.48", "63.5")
    assert result.rows[-1]["total_cap"] is None  # 没有市值就是没有，不补 0
    assert result.title == "涨停股清单（2026-10-21）"  # 没筛选时标题不变
    labels = {column.key: (column.label, column.unit) for column in result.columns}
    assert labels["total_cap"] == ("总市值", "亿元") and labels["float_cap"] == ("流通市值", "亿元")


def test_market_cap_range_and_main_board_are_applied_before_taking_the_top_rows() -> None:
    args = '{"view": "limit_up_stocks", "min_cap": 30, "max_cap": 100, "main_board_only": true}'
    result = _run(args, FakeSentiment(limit_up=_cap_pool()))
    assert [row["name"] for row in result.rows] == ["海洋王", "大业股份"]
    assert "沪深主板" in result.title and "总市值 30–100 亿元" in result.title
    assert "涨停池 6 只，符合 2 只" in result.title
    assert result.status == "ok" and result.truncated == 0


def test_without_the_main_board_flag_other_boards_stay_in() -> None:
    args = '{"view": "limit_up_stocks", "min_cap": 30, "max_cap": 100}'
    result = _run(args, FakeSentiment(limit_up=_cap_pool()))
    assert [row["name"] for row in result.rows] == ["海洋王", "大业股份", "创业股", "科创股"]


def test_float_cap_basis_and_a_single_bound() -> None:
    args = '{"view": "limit_up_stocks", "min_cap": 25, "cap_basis": "float"}'
    result = _run(args, FakeSentiment(limit_up=_cap_pool()))
    assert [row["name"] for row in result.rows] == ["海洋王", "创业股", "大盘股"]
    assert "流通市值不低于 25 亿元" in result.title
    upper = _run('{"view": "limit_up_stocks", "max_cap": 45}', FakeSentiment(limit_up=_cap_pool()))
    assert [row["name"] for row in upper.rows] == ["大业股份", "科创股"]
    assert "总市值不高于 45 亿元" in upper.title


def test_a_screen_that_matches_nothing_says_so_instead_of_loosening_the_filter() -> None:
    args = '{"view": "limit_up_stocks", "min_cap": 1000, "main_board_only": true}'
    result = _run(args, FakeSentiment(limit_up=_cap_pool()))
    assert result.status == "unavailable" and result.rows == ()
    assert "没有符合这些条件" in (result.reason or "") and "符合 0 只" in result.title
    only_gem = _limit_up(limit_up_stocks=[_capped("300001", "创业股", 1, "50", "30")])
    board = '{"view": "limit_up_stocks", "min_cap": 30, "main_board_only": true}'
    empty = _run(board, FakeSentiment(limit_up=only_gem))
    assert "没有符合这些条件" in (empty.reason or "")  # 主板筛空，不是「缺市值数据」


def test_a_pool_without_any_market_cap_cannot_be_screened_by_it() -> None:
    bare = _limit_up(limit_up_stocks=[_stock("600001", "甲", 1), _stock("600002", "乙", 1)])
    result = _run('{"view": "limit_up_stocks", "min_cap": 30}', FakeSentiment(limit_up=bare))
    assert result.status == "unavailable"
    assert "没有一只返回了总市值数据" in (result.reason or "")


def test_the_list_can_be_asked_for_up_to_sixty_rows() -> None:
    many = _limit_up(limit_up_stocks=[_stock(f"60{n:04d}", f"票{n}", 1) for n in range(45)])
    full = _run('{"view": "limit_up_stocks", "top": 60}', FakeSentiment(limit_up=many))
    assert len(full.rows) == 45 and full.truncated == 0
    too_many = _run('{"view": "limit_up_stocks", "top": 61}', FakeSentiment(limit_up=many))
    assert too_many.status == "unavailable"


def test_surveys_list_the_most_visited_companies() -> None:
    service = FakeSentiment(surveys=_surveys())
    result = _run('{"view": "surveys", "days": 14}', service)
    assert service.survey_days == [14] and result.title == "机构调研（近 14 天）"
    first = result.rows[0]
    assert (first["name"], first["events"], first["institutions"]) == ("北方华创", "7", "210")
    assert first["ways"] == "现场参观、电话会议、线上交流"  # 最多三种
    assert result.rows[1]["industry"] is None and result.rows[1]["ways"] is None


def test_surveys_without_industry_data_say_the_industry_column_is_incomplete() -> None:
    broken = Section(status="unavailable", reason="行业接口暂时取不到")
    result = _run('{"view": "surveys"}', FakeSentiment(surveys=_surveys(industry_section=broken)))
    assert result.status == "stale" and "行业：行业接口暂时取不到" in (result.reason or "")


def test_no_survey_records_is_unavailable() -> None:
    result = _run('{"view": "surveys"}', FakeSentiment(surveys=_surveys(top_stocks=[])))
    assert result.status == "unavailable" and "近 30 天没有调研记录" in (result.reason or "")


def test_hot_lists_are_capped_per_list_and_labelled() -> None:
    result = _run('{"view": "hot"}', hot=_hot())
    lists = [row["list"] for row in result.rows]
    assert lists.count("东财人气榜") == 10  # 12 只里只取前 10
    assert set(lists) == {"东财人气榜", "同花顺热股", "同花顺热门行业", "同花顺热门概念"}
    assert result.rows[0]["note"] == "算力、芯片" and result.status == "ok"


def test_a_failed_hot_list_is_named_and_the_rest_still_shown() -> None:
    down = Section(status="unavailable", reason="人气榜接口返回 500")
    result = _run('{"view": "hot"}', hot=_hot(eastmoney=[], eastmoney_section=down))
    assert result.status == "stale" and "东财人气榜：人气榜接口返回 500" in (result.reason or "")
    assert "东财人气榜" not in {row["list"] for row in result.rows}


def test_all_hot_lists_down_is_unavailable() -> None:
    down = Section(status="unavailable", reason="接口暂时取不到")
    hot = _hot(eastmoney_section=down, ths_section=down, plates_section=down)
    assert _run('{"view": "hot"}', hot=hot).status == "unavailable"


def test_live_only_views_refuse_a_past_date_without_calling_the_service() -> None:
    market = FakeMarket(_hot())
    sentiment = FakeSentiment(surveys=_surveys())
    tool = sentiment_tools.MarketSentimentTool(
        cast(MarketSentimentService, sentiment), cast(IndustryMarketService, market)
    )
    registry = ToolRegistry([tool])
    for view in ("hot", "surveys"):
        result = registry.execute(
            "market_sentiment", f'{{"view": "{view}", "as_of": "2026-10-10"}}', context()
        ).result
        assert result.status == "unavailable" and "只有实时数据" in (result.reason or "")
    assert market.calls == 0 and sentiment.survey_days == []


def test_upstream_failures_become_unavailable_results() -> None:
    result = _run('{"view": "limit_up"}', FakeSentiment(limit_up=upstream_down("东财返回 500")))
    assert result.status == "unavailable" and "东财返回 500" in (result.reason or "")
    gone = ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)
    assert _run('{"view": "hot"}', hot=gone).reason == "数据源暂时取不到：上游超时"


def test_bad_arguments_are_rejected() -> None:
    assert _run('{"view": "rumors"}').status == "unavailable"
    assert _run('{"view": "surveys", "days": 400}').status == "unavailable"
