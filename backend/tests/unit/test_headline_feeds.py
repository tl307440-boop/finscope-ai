"""金十、华尔街见闻、财联社公开快讯：只要标题，丢掉广告和认不出的时间。"""

from datetime import UTC, datetime

import pytest
from app.providers import headline_feeds as feeds
from app.providers.errors import ProviderError

from live_fakes import as_json


def test_jin10_uses_the_bracketed_headline_and_drops_ads() -> None:
    payload = as_json(
        {
            "status": 200,
            "data": [
                {
                    "id": "J1",
                    "type": 0,
                    "time": "2026-10-26 10:16:34",
                    "extras": {"ad": False},
                    "data": {"title": "", "content": "【创业板指涨超3%】后文不收。"},
                },
                {"id": "AD", "type": 1, "time": "2026-10-26 10:00:00", "data": {"content": "广告"}},
                {
                    "id": "VIP",
                    "type": 0,
                    "time": "2026-10-26 10:00:00",
                    "data": {"content": "会员稿", "exclusive_to": ["vip"]},
                },
            ],
        }
    )
    (item,) = feeds.parse_jin10(payload)
    assert item.title == "创业板指涨超3%"
    assert item.publisher == feeds.JIN10_PUBLISHER
    assert item.published_at == datetime(2026, 10, 26, 2, 16, 34, tzinfo=UTC)
    assert item.url == "https://www.jin10.com/flash"
    assert "后文" not in item.title


def test_wallstreetcn_prefers_plain_text_and_keeps_its_own_link() -> None:
    payload = as_json(
        {
            "code": 20000,
            "data": {
                "items": [
                    {
                        "id": 3185346,
                        "title": "",
                        "content": "<p>带标签的正文</p>",
                        "content_text": "创业板指涨超3%",
                        "display_time": 1792980954,
                        "uri": "https://wallstreetcn.com/livenews/3185346",
                    }
                ]
            },
        }
    )
    (item,) = feeds.parse_wscn(payload)
    assert item.code == "wscn:3185346"
    assert item.title == "创业板指涨超3%"
    assert item.url == "https://wallstreetcn.com/livenews/3185346"
    assert item.published_at == datetime.fromtimestamp(1792980954, UTC)


def test_cls_cache_uses_the_title_not_the_brief() -> None:
    payload = as_json(
        {
            "errno": 0,
            "data": {
                "roll_data": [
                    {
                        "id": 2514712,
                        "title": "创业板指涨超3%",
                        "brief": "这是更长的电报正文，不应该整段进入标题。" * 5,
                        "content": "全文",
                        "ctime": 1792980995,
                    }
                ]
            },
        }
    )
    (item,) = feeds.parse_cls(payload)
    assert item.title == "创业板指涨超3%"
    assert item.url == "https://www.cls.cn/detail/2514712"
    assert "电报正文" not in item.title
    assert "vip=0" in feeds.jin10_url()
    assert "sign=" not in feeds.cls_url()


@pytest.mark.parametrize("parser", [feeds.parse_jin10, feeds.parse_wscn, feeds.parse_cls])
def test_malformed_payloads_are_a_structured_error(parser) -> None:
    with pytest.raises(ProviderError) as raised:
        parser(b"not-json")
    assert raised.value.code == "UPSTREAM_FORMAT"
