"""东财板块 / 热度榜适配器：只测 URL 与解析，不联网。样本按 2026-10 实测响应裁剪。"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
from app.providers import eastmoney_boards as em
from app.providers.errors import ProviderError

TREE = json.dumps(
    {
        "bklist": [
            {"market": 90, "code": "BK0169", "name": "四川板块", "type": 1, "flag": 0},
            {"market": 90, "code": "BK1201", "name": "电子", "type": 2, "flag": 1},
            {"market": 90, "code": "BK1036", "name": "半导体", "type": 2, "flag": 2},
            {"market": 90, "code": "BK1575", "name": "白酒Ⅲ", "type": 2, "flag": 3},
            {"market": 90, "code": "BK1157", "name": "飞行汽车(eVTOL)", "type": 3, "flag": 0},
            {"market": 90, "code": "BK1036", "name": "半导体", "type": 2, "flag": 2},
            {"market": 90, "code": "BK9999", "name": "未知", "type": 7, "flag": 0},
        ]
    }
).encode()

QUOTES = json.dumps(
    {
        "rc": 0,
        "data": {
            "total": 2,
            "diff": [
                {
                    "f2": 2251.35,
                    "f3": -0.76,
                    "f6": 180294918252.0,
                    "f8": 2.87,
                    "f9": 65.57,
                    "f12": "BK1036",
                    "f14": "半导体",
                    "f20": 11954501376000,
                    "f24": -23.78,
                    "f25": 14.2,
                    "f62": 3210736384.0,
                    "f104": 61,
                    "f105": 126,
                    "f106": 1,
                    "f109": -9.86,
                    "f160": -21.52,
                    "f164": -12824204800.0,
                    "f128": "复旦微电",
                    "f140": "688385",
                    "f136": 14.61,
                },
                {"f12": "BK0475", "f14": "银行Ⅱ", "f2": "-", "f3": "-", "f104": "-"},
            ],
        },
    }
).encode()


def test_tree_keeps_industry_levels_and_concepts() -> None:
    nodes = em.parse_board_tree(TREE)
    by_code = {node.code: node for node in nodes}
    assert by_code["BK1201"].kind == "industry" and by_code["BK1201"].level == 1
    assert by_code["BK1036"].level == 2
    assert by_code["BK1575"].level == 3
    assert by_code["BK1157"].kind == "concept" and by_code["BK1157"].level == 0
    assert by_code["BK0169"].kind == "region"
    # 重复代码只留一条；未知 type 丢弃。
    assert sum(1 for node in nodes if node.code == "BK1036") == 1
    assert "BK9999" not in by_code


def test_empty_tree_is_an_upstream_error_not_an_empty_catalog() -> None:
    with pytest.raises(ProviderError) as caught:
        em.parse_board_tree(b'{"bklist": []}')
    assert caught.value.code == "UPSTREAM_PAYLOAD"


def test_quotes_map_fields_and_keep_dashes_as_missing() -> None:
    first, second = em.parse_board_quotes(QUOTES)
    assert first.code == "BK1036"
    assert first.change_pct == Decimal("-0.76")
    assert first.change_5d_pct == Decimal("-9.86")
    assert first.change_10d_pct == Decimal("-21.52")
    assert first.change_60d_pct == Decimal("-23.78")
    assert first.main_net_inflow == Decimal("3210736384.0")
    assert (first.advancers, first.decliners, first.unchanged) == (61, 126, 1)
    assert first.leader_name == "复旦微电"
    # 停牌 / 无数据的 "-" 不能被当成 0。
    assert second.last is None and second.change_pct is None and second.advancers is None


def test_quotes_reject_non_json() -> None:
    with pytest.raises(ProviderError) as caught:
        em.parse_board_quotes(b"<html>403 Forbidden</html>")
    assert caught.value.code == "UPSTREAM_PAYLOAD"


def test_members_url_pages_by_100_with_board_filter() -> None:
    query = parse_qs(urlparse(em.members_url("bk1036", 3)).query)
    assert query["pz"] == ["100"]
    assert query["pn"] == ["3"]
    assert query["fs"] == ["b:BK1036 f:!50"]


def test_board_code_validation() -> None:
    assert em.board_number("BK0475") == "475"
    with pytest.raises(ProviderError):
        em.normalize_board_code("600519")


def test_members_parse_sub_industry_and_exchange() -> None:
    payload = json.dumps(
        {
            "data": {
                "total": 188,
                "diff": [
                    {
                        "f12": "688385",
                        "f13": 1,
                        "f14": "复旦微电",
                        "f3": 14.61,
                        "f9": 26.29,
                        "f23": 6.39,
                        "f20": 44653820543,
                        "f62": 21022576.0,
                        "f100": "半导体",
                    },
                    {"f12": "300001", "f13": 0, "f14": "特锐德", "f3": "-", "f100": "-"},
                ],
            }
        }
    ).encode()
    page = em.parse_members(payload)
    assert page.total == 188
    assert page.rows[0].exchange == "SSE" and page.rows[0].sub_industry == "半导体"
    assert page.rows[1].exchange == "SZSE" and page.rows[1].change_pct is None
    assert page.rows[1].sub_industry is None
    assert em.parse_members(b'{"data": null}').total == 0


def test_klines_sorted_and_deduped() -> None:
    payload = json.dumps(
        {
            "data": {
                "klines": [
                    "2026-10-16,2321.57,2251.35,2330.00,2240.00,26847458,221764059163.00",
                    "2026-10-15,2300.00,2268.60,2310.00,2260.00,22628373,181107749204.00",
                    "2026-10-16,1,1,1,1,1,1",
                ]
            }
        }
    ).encode()
    bars = em.parse_klines(payload)
    assert [bar.trade_date for bar in bars] == [date(2026, 10, 15), date(2026, 10, 16)]
    assert bars[-1].close == Decimal("2251.35")


def test_klines_reject_short_rows() -> None:
    with pytest.raises(ProviderError):
        em.parse_klines(b'{"data": {"klines": ["2026-10-16,1,2"]}}')


def test_valuation_parse_and_url_filter() -> None:
    assert (
        'ORIGINALCODE="1036"'
        in parse_qs(urlparse(em.valuation_url("BK1036", 1)).query)["filter"][0]
    )
    payload = json.dumps(
        {
            "result": {
                "pages": 2,
                "data": [
                    {
                        "TRADE_DATE": "2026-10-16 00:00:00",
                        "PE_TTM": 94.0973,
                        "PB_MRQ": 16.04,
                        "PS_TTM": None,
                        "NUM": 188,
                        "LOSS_COUNT": 30,
                    },
                ],
            }
        }
    ).encode()
    page = em.parse_valuation(payload)
    assert page.pages == 2
    assert page.rows[0].trade_date == date(2026, 10, 16)
    assert page.rows[0].pe_ttm == Decimal("94.0973") and page.rows[0].ps_ttm is None
    assert em.parse_valuation(b'{"result": null}').rows == ()


def test_reports_build_traceable_links() -> None:
    payload = json.dumps(
        {
            "hits": 1,
            "data": [
                {
                    "title": "电子行业周报",
                    "orgSName": "上海证券",
                    "publishDate": "2026-10-16 00:00:00.000",
                    "infoCode": "AP202610161830623269",
                    "emRatingName": "增持",
                    "researcher": "张三",
                },
                {"title": "缺代码的一条", "publishDate": "2026-10-16 00:00:00.000"},
            ],
        }
    ).encode()
    (report,) = em.parse_reports(payload)
    assert report.published_on == date(2026, 10, 16)
    assert report.url.endswith("infocode=AP202610161830623269")
    assert report.rating == "增持"


def test_hot_rank_maps_exchange_prefix() -> None:
    payload = json.dumps(
        {
            "data": [
                {"sc": "SZ002085", "rk": 1, "rc": 0},
                {"sc": "SH688825", "rk": 2, "rc": -3},
                {"sc": "XX000001", "rk": 3},
            ]
        }
    ).encode()
    first, second = em.parse_hot_rank(payload)
    assert (first.exchange, first.code, first.rank) == ("SZSE", "002085", 1)
    assert second.rank_change == -3
    assert em.stock_secid("SSE", "688825") == "1.688825"


def test_ths_hot_lists() -> None:
    stocks = json.dumps(
        {
            "status_code": 0,
            "data": {
                "stock_list": [
                    {
                        "code": "002242",
                        "name": "九阳股份",
                        "order": 1,
                        "rate": "147814.0",
                        "rise_and_fall": 2.773,
                        "hot_rank_chg": 0,
                        "tag": {"concept_tag": ["绿色电力"], "popularity_tag": "10天6板"},
                    },
                ]
            },
        }
    ).encode()
    (stock,) = em.parse_ths_hot_stocks(stocks)
    assert stock.popularity_tag == "10天6板" and stock.concept_tags == ("绿色电力",)
    plates = json.dumps(
        {
            "status_code": 0,
            "data": {
                "plate_list": [
                    {
                        "code": "881164",
                        "name": "文化传媒",
                        "order": 1,
                        "rate": "17631.0",
                        "rise_and_fall": -1.4513,
                        "hot_tag": "连续30天上榜",
                    },
                ]
            },
        }
    ).encode()
    (plate,) = em.parse_ths_hot_plates(plates)
    assert plate.hot_tag == "连续30天上榜"
    with pytest.raises(ProviderError):
        em.parse_ths_hot_stocks(b'{"status_code": -1, "data": {}}')
