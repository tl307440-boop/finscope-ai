"""百度千帆搜索（第三家联网检索服务）：请求体、响应解析、失败回退、密钥不外泄。

注意：这里验证的是按官方文档写的适配器在模拟响应下的行为；真实接口没有联调过（没有密钥）。
"""

import json

import httpx
import pytest
from app.core.config import Settings
from app.providers.web_search import (
    QIANFAN_URL,
    SearchServiceError,
    qianfan_body,
    qianfan_failure,
    qianfan_hits,
)
from app.services.assistant.tools.web import _live_search
from app.services.runtime_settings import MANAGED

QIANFAN_OK = json.dumps(
    {
        "request_id": "r-1",
        "references": [
            {
                "id": 1,
                "title": "半导体设备国产化率持续提升",
                "url": "https://news.example/a",
                "content": "近期多家机构表示……",
                "date": "2026-10-20 08:30:00",
                "website": "示例网",
                "type": "web",
            },
            {
                "id": 2,
                "title": "没有日期的报道",
                "url": "https://news.example/b",
                "content": "摘要",
            },
        ],
    },
    ensure_ascii=False,
).encode()
BOCHA_OK = json.dumps(
    {
        "data": {
            "webPages": {
                "value": [
                    {
                        "name": "博查的结果",
                        "url": "https://news.example/bocha",
                        "snippet": "摘要",
                        "datePublished": "2026-10-19T00:00:00+08:00",
                    }
                ]
            }
        }
    },
    ensure_ascii=False,
).encode()
BRAVE_OK = json.dumps(
    {
        "web": {
            "results": [
                {"title": "Brave hit", "url": "https://news.example/brave", "description": "text"}
            ]
        }
    }
).encode()
BAIDU_ERROR = json.dumps({"request_id": "r-2", "code": 216003, "message": "token invalid"}).encode()


def _keys(bocha: str = "", brave: str = "", baidu: str = "") -> Settings:
    return Settings(bocha_api_key=bocha, brave_api_key=brave, baidu_qianfan_api_key=baidu)


def test_the_request_body_asks_for_web_results_and_maps_the_time_window() -> None:
    body = json.loads(qianfan_body("半导体 国产替代 最近"))
    assert body["messages"] == [{"role": "user", "content": "半导体 国产替代 最近"}]
    assert body["resource_type_filter"] == [{"type": "web", "top_k": 8}]
    assert "search_recency_filter" not in body
    windows = {"oneDay": "day", "oneWeek": "week", "oneMonth": "month", "oneYear": "year"}
    for ours, theirs in windows.items():
        assert json.loads(qianfan_body("q", ours))["search_recency_filter"] == theirs


def test_references_become_hits_with_the_provider_name_and_date() -> None:
    first, second = qianfan_hits(QIANFAN_OK)
    assert (first.title, first.url, first.provider) == (
        "半导体设备国产化率持续提升",
        "https://news.example/a",
        "百度",
    )
    assert first.snippet == "近期多家机构表示……" and str(first.published) == "2026-10-20"
    assert second.published is None  # 没有日期就是没有，不补


@pytest.mark.parametrize("payload", [b"", b"not json", b"[]", b'{"references": "x"}'])
def test_garbage_responses_give_no_hits_instead_of_crashing(payload: bytes) -> None:
    assert qianfan_hits(payload) == ()


def test_an_error_code_in_a_200_response_is_a_failure_but_results_are_not() -> None:
    assert qianfan_failure(BAIDU_ERROR) == "返回错误码 216003"
    assert qianfan_failure(QIANFAN_OK) is None
    assert qianfan_failure(b'{"references": [], "code": 5}') is None  # 有结果列表就不算出错
    assert qianfan_failure(b'{"code": 0}') is None
    assert qianfan_failure(b"not json") is None


def _router(seen: list[httpx.Request], responses: dict[str, httpx.Response]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[request.url.host]

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_chinese_queries_try_bocha_then_baidu_then_brave() -> None:
    seen: list[httpx.Request] = []
    client = _router(
        seen,
        {
            "api.bochaai.com": httpx.Response(403),
            "qianfan.baidubce.com": httpx.Response(200, content=QIANFAN_OK),
        },
    )
    hits = _live_search("半导体 前几天", _keys("b", "r", "q"), "oneWeek", client)
    assert [request.url.host for request in seen] == ["api.bochaai.com", "qianfan.baidubce.com"]
    assert {hit.provider for hit in hits} == {"百度"}
    request = seen[1]
    assert str(request.url) == QIANFAN_URL
    assert request.headers["Authorization"] == "Bearer q"
    assert json.loads(request.content)["search_recency_filter"] == "week"


def test_english_queries_try_brave_first_and_baidu_last() -> None:
    seen: list[httpx.Request] = []
    responses = {
        "api.search.brave.com": httpx.Response(429),
        "api.bochaai.com": httpx.Response(403),
        "qianfan.baidubce.com": httpx.Response(200, content=QIANFAN_OK),
    }
    hits = _live_search("Powell speech", _keys("b", "r", "q"), None, _router(seen, responses))
    assert [request.url.host for request in seen] == [
        "api.search.brave.com",
        "api.bochaai.com",
        "qianfan.baidubce.com",
    ]
    assert hits and hits[0].provider == "百度"


def test_baidu_alone_is_enough_and_unconfigured_providers_are_not_called() -> None:
    seen: list[httpx.Request] = []
    client = _router(seen, {"qianfan.baidubce.com": httpx.Response(200, content=QIANFAN_OK)})
    assert _live_search("半导体", _keys(baidu="q"), None, client)
    assert [request.url.host for request in seen] == ["qianfan.baidubce.com"]


def test_an_error_code_with_http_200_falls_through_to_the_next_provider() -> None:
    seen: list[httpx.Request] = []
    responses = {
        "api.bochaai.com": httpx.Response(403),
        "qianfan.baidubce.com": httpx.Response(200, content=BAIDU_ERROR),
        "api.search.brave.com": httpx.Response(200, content=BRAVE_OK),
    }
    hits = _live_search("半导体", _keys("b", "r", "q"), None, _router(seen, responses))
    assert [hit.provider for hit in hits] == ["Brave"]


def test_when_every_provider_fails_the_reason_names_baidu_without_the_key() -> None:
    responses = {"qianfan.baidubce.com": httpx.Response(200, content=BAIDU_ERROR)}
    with pytest.raises(SearchServiceError) as caught:
        _live_search("半导体", _keys(baidu="secret-baidu-key"), None, _router([], responses))
    reason = caught.value.reason
    assert "百度：返回错误码 216003" in reason and "不是网上没有这条消息" in reason
    assert "secret-baidu-key" not in reason and len(reason) <= 200


def test_a_healthy_baidu_with_no_references_is_an_empty_result_not_an_error() -> None:
    client = _router([], {"qianfan.baidubce.com": httpx.Response(200, json={"references": []})})
    assert _live_search("半导体", _keys(baidu="q"), None, client) == []


def test_the_key_is_a_server_side_secret_managed_in_the_settings_page() -> None:
    (managed,) = [item for item in MANAGED if item.key == "BAIDU_QIANFAN_API_KEY"]
    assert (managed.field, managed.secret, managed.kind) == ("baidu_qianfan_api_key", True, "text")
    assert Settings().baidu_qianfan_api_key == ""  # 默认没有密钥：不会悄悄联网
