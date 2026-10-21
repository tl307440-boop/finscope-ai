"""东方财富新闻接口适配：快讯列表、关键词搜索（JSONP）的网址与解析。离线，用真实响应的节选。"""

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from app.providers import eastmoney_news as news
from app.providers.errors import ProviderError

from live_fakes import as_json

FLASH = as_json(
    {
        "req_trace": "1757431863044",
        "code": "1",
        "message": "success",
        "data": {
            "total": 5000,
            "fastNewsList": [
                {
                    "summary": "【刚果（金）埃博拉疫情已致4646人死亡】根据……",
                    "code": "202610213902977191",
                    "showTime": "2026-10-21 22:10:05",
                    "title": "刚果（金）埃博拉疫情已致4646人死亡",
                    "stockList": [],
                },
                {"code": "202610213902968947", "showTime": "2026-10-21 22:05:38", "title": ""},
                {"code": "", "showTime": "2026-10-21 22:00:00", "title": "没有编号"},
                {"code": "202610213902963142", "showTime": "昨天", "title": "时间写不出来"},
                {
                    "code": "202610213902960411",
                    "showTime": "2026-10-21 21:56:04",
                    "title": "苹果：在10月23日iPhone Duo正式发售当天 现货数量将比较有限",
                },
            ],
        },
    }
)


def _jsonp(rows: list[dict[str, str]], code: int = 0) -> bytes:
    body = {"code": code, "hitsTotal": len(rows), "result": {"cmsArticleWebOld": rows}}
    return f"cb({json.dumps(body, ensure_ascii=False)})".encode()


SEARCH_ROW = {
    "date": "2026-10-21 17:23:18",
    "code": "202610213902713223",
    "title": "资金逆势涌入！半导体ETF增逾170亿份",
    "content": "不应该被取走的摘要。",
    "mediaName": "上海证券报",
    "url": "http://finance.eastmoney.com/a/202610213902713223.html",
}


def test_flash_items_have_utc_time_title_and_an_eastmoney_link() -> None:
    items = news.parse_flash(FLASH)
    assert [item.code for item in items] == ["202610213902977191", "202610213902960411"]
    first = items[0]
    assert first.title == "刚果（金）埃博拉疫情已致4646人死亡"
    assert first.published_at == datetime(2026, 10, 21, 14, 10, 5, tzinfo=UTC)  # 上海 22:10:05
    assert first.url == "https://finance.eastmoney.com/a/202610213902977191.html"
    assert first.publisher == news.FLASH_PUBLISHER
    assert "摘要" not in repr(first) and "根据" not in repr(first)  # 摘要和正文不取


def test_items_without_a_title_a_code_or_a_readable_time_are_dropped_not_guessed() -> None:
    assert len(news.parse_flash(FLASH)) == 2


def test_search_results_are_https_links_with_the_media_name() -> None:
    (item,) = news.parse_search(_jsonp([SEARCH_ROW]))
    assert item.url == "https://finance.eastmoney.com/a/202610213902713223.html"
    assert item.publisher == "上海证券报"
    assert item.published_at == datetime(2026, 10, 21, 9, 23, 18, tzinfo=UTC)
    assert "摘要" not in repr(item)


def test_highlight_tags_are_stripped_and_long_titles_are_cut() -> None:
    row = {**SEARCH_ROW, "title": "<em>半导体</em>" + "长" * 300}
    (item,) = news.parse_search(_jsonp([row]))
    assert "<" not in item.title and item.title.startswith("半导体长")
    assert len(item.title) == news.MAX_TITLE_CHARS


def test_links_to_other_sites_are_not_taken() -> None:
    row = {**SEARCH_ROW, "url": "https://evil.example/a.html"}
    assert news.parse_search(_jsonp([row])) == ()
    lookalike = {**SEARCH_ROW, "url": "https://eastmoney.com.evil.example/a.html"}
    assert news.parse_search(_jsonp([lookalike])) == ()


@pytest.mark.parametrize(
    "payload",
    [b"", b"not json", b'{"code": "0"}', b"cb(not json)", _jsonp([], code=1), b'cb({"code": 0})'],
)
def test_malformed_search_payloads_raise_a_structured_error(payload: bytes) -> None:
    with pytest.raises(ProviderError) as caught:
        news.parse_search(payload)
    assert caught.value.code == "UPSTREAM_FORMAT"


@pytest.mark.parametrize("payload", [b"", b"[]", b'{"code": "0"}', b'{"code": "1", "data": 5}'])
def test_malformed_flash_payloads_raise_a_structured_error(payload: bytes) -> None:
    with pytest.raises(ProviderError) as caught:
        news.parse_flash(payload)
    assert caught.value.code == "UPSTREAM_FORMAT"


def test_search_url_carries_the_keyword_and_no_highlight_markup() -> None:
    url = news.search_url("半导体 国产替代", page_size=12)
    query = parse_qs(urlsplit(url).query)
    assert query["cb"] == ["cb"]
    param = json.loads(query["param"][0])
    assert param["keyword"] == "半导体 国产替代" and param["type"] == ["cmsArticleWebOld"]
    paging = param["param"]["cmsArticleWebOld"]
    assert paging["pageSize"] == 12 and paging["preTag"] == "" and paging["postTag"] == ""
    assert url.startswith("https://search-api-web.eastmoney.com/")


def test_flash_url_asks_for_the_requested_page_size() -> None:
    url = news.flash_url(40)
    assert url.startswith("https://np-weblist.eastmoney.com/") and "pageSize=40" in url
    assert "biz=web_724" in url
