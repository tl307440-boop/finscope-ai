"""实时新闻标题工具 `news_flash`：快讯、关键词搜索合并、部分失败、时点、回合次数、来源未核实。"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from app.services.assistant.evidence import EvidenceBook, render
from app.services.assistant.results import ToolResult
from app.services.assistant.tools import ToolRegistry
from app.services.assistant.tools.base import ToolContext
from app.services.assistant.tools.flash import MAX_CALLS_PER_TURN, NewsFlashTool
from app.services.market_cache import TtlCache
from app.services.news_flash import NewsFlashService

from live_fakes import ScriptedGateway, as_json, context, upstream_down

NOW = datetime(2026, 10, 21, 14, 30, tzinfo=UTC)  # 上海 22:30
FLASH_URL = "getFastNewsList"
SEARCH_URL = "search-api-web"


def _flash(*items: tuple[str, str, str]) -> bytes:
    rows = [{"code": code, "showTime": when, "title": title} for code, when, title in items]
    return as_json({"code": "1", "data": {"fastNewsList": rows}})


def _search(*items: tuple[str, str, str, str]) -> bytes:
    rows = [
        {
            "code": code,
            "date": when,
            "title": title,
            "mediaName": media,
            "url": f"http://finance.eastmoney.com/a/{code}.html",
        }
        for code, when, title, media in items
    ]
    body = json.dumps({"code": 0, "result": {"cmsArticleWebOld": rows}}, ensure_ascii=False)
    return f"cb({body})".encode()


FLASHES = _flash(
    ("F1", "2026-10-21 22:10:05", "半导体设备板块拉升 北方华创涨超5%"),
    ("F2", "2026-10-21 22:05:38", "俄外交部说将对日美联合军演采取回应措施"),
    ("F3", "2026-10-21 21:00:00", "国产芯片概念早盘走强"),
)
SEARCH = _search(
    ("S1", "2026-10-21 17:23:18", "资金逆势涌入！半导体ETF增逾170亿份", "上海证券报"),
    ("F1", "2026-10-21 22:10:05", "半导体设备板块拉升 北方华创涨超5%", "东方财富快讯"),
    ("S0", "2026-10-15 09:00:00", "一周前的半导体新闻", "证券时报"),
)


_EMPTY_FEEDS = {
    "api-one.wallstcn.com": as_json({"code": 20000, "data": {"items": []}}),
    "flash-api.jin10.com": as_json({"status": 200, "data": []}),
    "cls.cn/api/cache": as_json({"errno": 0, "data": {"roll_data": []}}),
}


def _service(routes: dict[str, bytes | Exception]) -> tuple[NewsFlashService, ScriptedGateway]:
    gateway = ScriptedGateway({**_EMPTY_FEEDS, **routes})
    return NewsFlashService(gateway=gateway, cache=TtlCache()), gateway


def _run(service: NewsFlashService, arguments: str, ctx: ToolContext | None = None) -> ToolResult:
    registry = ToolRegistry([NewsFlashTool(service)])
    return registry.execute("news_flash", arguments, ctx or context(NOW)).result


def test_latest_flashes_without_a_keyword_only_call_the_flash_feed() -> None:
    service, gateway = _service({FLASH_URL: FLASHES})
    result = _run(service, "{}")
    assert result.status == "ok" and result.title == "最新快讯"
    assert [row["time"] for row in result.rows] == ["22:10", "22:05", "21:00"]
    assert result.rows[0]["publisher"] == "东方财富快讯" and result.rows[0]["note"] == "未核实"
    assert len(gateway.urls) == 4 and any(FLASH_URL in url for url in gateway.urls)
    assert result.as_of.isoformat() == "2026-10-21"


def test_keyword_merges_search_and_matching_flashes_without_duplicates() -> None:
    service, gateway = _service({FLASH_URL: FLASHES, SEARCH_URL: SEARCH})
    result = _run(service, '{"keyword": "半导体"}')
    titles = [row["title"] for row in result.rows]
    assert titles == [
        "半导体设备板块拉升 北方华创涨超5%",  # 快讯和搜索都有：只留一条
        "资金逆势涌入！半导体ETF增逾170亿份",
    ]
    assert "俄外交部" not in "".join(titles)  # 快讯池里和关键词无关的不要
    assert "国产芯片概念早盘走强" not in titles  # 标题里没有「半导体」
    assert "一周前的半导体新闻" not in titles  # 超出默认 72 小时
    assert len(gateway.urls) == 5


def test_the_hours_window_is_applied_to_publication_time() -> None:
    service, _ = _service({FLASH_URL: FLASHES, SEARCH_URL: SEARCH})
    result = _run(service, '{"keyword": "半导体", "hours": 168}')
    assert "一周前的半导体新闻" in [row["title"] for row in result.rows]
    narrow = _run(service, '{"keyword": "半导体", "hours": 1}')
    assert [row["title"] for row in narrow.rows] == ["半导体设备板块拉升 北方华创涨超5%"]


def test_headlines_are_unverified_web_passages_with_the_time_in_the_text() -> None:
    service, _ = _service({FLASH_URL: FLASHES})
    result = _run(service, "{}")
    assert result.passages and all(not passage.verified for passage in result.passages)
    first = result.passages[0]
    assert (first.corpus_kind, first.key, first.publisher) == ("web", "news:F1", "东方财富快讯")
    assert first.content.startswith("2026-10-21 22:10 发布（上海时间）：")
    assert first.url == "https://finance.eastmoney.com/a/F1.html"
    assert "没有被系统核对" in (result.reason or "")
    rendered = render(EvidenceBook().add(result))
    assert "[1] 半导体设备板块拉升 北方华创涨超5%｜东方财富快讯｜2026-10-21" in rendered


def test_one_feed_failing_still_gives_the_other_and_says_so() -> None:
    service, _ = _service({FLASH_URL: upstream_down("快讯接口返回 500"), SEARCH_URL: SEARCH})
    result = _run(service, '{"keyword": "半导体"}')
    assert result.status == "stale" and len(result.rows) >= 1
    assert "7x24 快讯暂时取不到（快讯接口返回 500）" in (result.reason or "")


def test_a_keyword_keeps_only_matching_headlines_from_every_flash_feed() -> None:
    moment = int(datetime(2026, 10, 21, 14, 0, tzinfo=UTC).timestamp())
    jin10 = as_json(
        {
            "status": 200,
            "data": [
                {
                    "id": "J1",
                    "type": 0,
                    "time": "2026-10-21 22:08:00",
                    "data": {"content": "【半导体设备订单回暖】正文不收。"},
                },
                {
                    "id": "J2",
                    "type": 0,
                    "time": "2026-10-21 22:07:00",
                    "data": {"content": "【黄金价格走高】无关。"},
                },
            ],
        }
    )
    wscn = as_json(
        {
            "code": 20000,
            "data": {
                "items": [
                    {
                        "id": 9,
                        "title": "半导体设备再获订单",
                        "display_time": moment,
                        "uri": "https://wallstreetcn.com/livenews/9",
                    },
                    {
                        "id": 8,
                        "title": "原油库存下降",
                        "display_time": moment,
                        "uri": "https://wallstreetcn.com/livenews/8",
                    },
                ]
            },
        }
    )
    cls = as_json(
        {
            "errno": 0,
            "data": {
                "roll_data": [
                    {"id": 3, "title": "财联社称半导体设备拉升", "ctime": moment},
                    {"id": 4, "title": "财联社称体育赛程公布", "ctime": moment},
                ]
            },
        }
    )
    service, _ = _service(
        {
            FLASH_URL: FLASHES,
            SEARCH_URL: SEARCH,
            "flash-api.jin10.com": jin10,
            "api-one.wallstcn.com": wscn,
            "cls.cn/api/cache": cls,
        }
    )
    result = _run(service, '{"keyword": "半导体"}')
    titles = [row["title"] for row in result.rows]
    assert "半导体设备订单回暖" in titles
    assert "半导体设备再获订单" in titles
    assert "财联社称半导体设备拉升" in titles
    assert "黄金价格走高" not in titles
    assert "原油库存下降" not in titles
    assert "财联社称体育赛程公布" not in titles
    assert "俄外交部" not in "".join(titles)
    assert {"金十数据", "华尔街见闻", "财联社电报", "东方财富快讯"} <= {
        row["publisher"] for row in result.rows
    }


def test_a_failed_eastmoney_feed_is_filled_by_jin10() -> None:
    jin10 = as_json(
        {
            "status": 200,
            "data": [
                {
                    "id": "J1",
                    "type": 0,
                    "time": "2026-10-21 22:00:00",
                    "data": {"content": "【半导体设备订单回暖】正文不该被整段收下。", "title": ""},
                }
            ],
        }
    )
    service, _ = _service(
        {FLASH_URL: upstream_down("快讯接口返回 500"), "flash-api.jin10.com": jin10}
    )
    result = _run(service, '{"keyword": "半导体"}')
    assert result.status == "stale"
    assert [row["title"] for row in result.rows] == ["半导体设备订单回暖"]
    assert result.rows[0]["publisher"] == "金十数据"
    assert "7x24 快讯暂时取不到" in (result.reason or "")
    assert "正文不该" not in result.rows[0]["title"]


def test_every_feed_failing_is_unavailable_with_the_upstream_reason() -> None:
    down = upstream_down("快讯接口返回 500")
    service, _ = _service(
        {
            FLASH_URL: down,
            "api-one.wallstcn.com": down,
            "flash-api.jin10.com": down,
            "cls.cn/api/cache": down,
        }
    )
    result = _run(service, "{}")
    assert result.status == "unavailable" and result.reason == "数据源暂时取不到：快讯接口返回 500"


def test_no_headline_in_the_window_is_unavailable_and_names_the_keyword() -> None:
    service, _ = _service({FLASH_URL: FLASHES, SEARCH_URL: _search()})
    result = _run(service, '{"keyword": "量子玄学"}')
    assert result.status == "unavailable"
    assert "最近 72 小时没有查到包含「量子玄学」的新闻标题" in (result.reason or "")


def test_only_live_data_exists_so_a_past_date_is_refused_without_a_request() -> None:
    service, gateway = _service({FLASH_URL: FLASHES})
    result = _run(service, '{"as_of": "2026-10-10"}')
    assert result.status == "unavailable" and "只有实时数据" in (result.reason or "")
    assert gateway.urls == []


def test_a_turn_can_look_up_headlines_only_a_few_times() -> None:
    service, gateway = _service({FLASH_URL: FLASHES})
    ctx = context(NOW)
    results = [_run(service, f'{{"top": {5 + n}}}', ctx) for n in range(MAX_CALLS_PER_TURN + 1)]
    assert [result.status for result in results[:-1]] == ["ok"] * MAX_CALLS_PER_TURN
    assert results[-1].status == "unavailable" and "不要再查" in (results[-1].reason or "")
    assert len(gateway.urls) == 4  # 四路快讯都走缓存，没有重复请求上游


def test_top_limits_rows_and_reports_how_many_were_left_out() -> None:
    many = _flash(*[(f"F{n}", f"2026-10-21 21:{59 - n:02d}:00", f"快讯 {n}") for n in range(12)])
    service, _ = _service({FLASH_URL: many})
    result = _run(service, '{"top": 5}')
    assert len(result.rows) == 5 and result.truncated == 7


def test_stale_cached_headlines_are_labelled_as_old() -> None:
    clock = {"now": 0.0}
    gateway = ScriptedGateway({FLASH_URL: FLASHES, **_EMPTY_FEEDS})
    service = NewsFlashService(gateway=gateway, cache=TtlCache(lambda: clock["now"]))
    assert _run(service, "{}").status == "ok"
    clock["now"] = 600.0  # 超过快讯缓存的 60 秒
    gateway.routes[FLASH_URL] = upstream_down("快讯接口返回 500")
    result = _run(service, "{}")
    assert result.status == "stale" and "是缓存的旧数据" in (result.reason or "")


@pytest.mark.parametrize(
    "arguments",
    ['{"keyword": "芯"}', '{"hours": 500}', '{"top": 1}', '{"query": "半导体"}'],
)
def test_bad_arguments_are_rejected_before_any_request(arguments: str) -> None:
    service, gateway = _service({FLASH_URL: FLASHES})
    result: Any = _run(service, arguments)
    assert result.status == "unavailable" and "参数不合法" in (result.reason or "")
    assert gateway.urls == []
