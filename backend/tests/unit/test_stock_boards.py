"""个股所属板块：解析、回退链、扶摇反查索引。全部用预置样本，不连网。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from datetime import datetime

from app.providers import stock_boards as sb
from app.providers.errors import ProviderError
from app.services.ashare_panorama import ASharePanoramaService, _Acc
from app.services.fuyao_board_index import BUILDING, NOT_CONFIGURED, FuyaoBoardIndex
from app.services.market_cache import TtlCache
from app.utils.market_time import SHANGHAI

_FAKE_KEY = "test-key-not-real"


def _f10(rows: list[dict[str, object]]) -> dict[str, object]:
    return {"result": {"pages": 1, "data": rows}}


def _row(name: str, kind: str | None = None, precise: str | None = None) -> dict[str, object]:
    return {"BOARD_NAME": name, "BOARD_TYPE": kind, "IS_PRECISE": precise}


EM_SAMPLE = _f10(
    [
        _row("茅指数", None, "0"),
        _row("白酒", None, "1"),
        _row("标准普尔"),
        _row("沪股通"),
        _row("乡村振兴", None, "1"),
        _row("融资融券"),
        _row("上证50_"),
        _row("白酒Ⅲ", "行业"),
        _row("食品饮料", "行业"),
        _row("白酒Ⅱ", "行业"),
        _row("贵州板块", "板块"),
        _row("白酒"),  # 重复
    ]
)

TENCENT_SAMPLE = {
    "code": 0,
    "data": {
        "area": [{"name": "贵州"}],
        "concept": [{"name": "融资融券"}, {"name": "白酒概念"}, {"name": "国企改革"}],
        "plate": [
            {"name": "白酒Ⅱ", "level": "2"},
            {"name": "食品饮料", "level": "1"},
        ],
    },
}


def test_em_f10_splits_industry_concept_and_drops_noise_and_region() -> None:
    boards = sb.parse_em_f10(EM_SAMPLE)
    assert boards.industries == ("食品饮料", "白酒Ⅱ", "白酒Ⅲ")  # 一级在前，Ⅱ / Ⅲ 依次
    # 核心题材（IS_PRECISE=1）在前；指数成分 / 资金标签被去掉；地域板块不算
    assert boards.concepts == ("白酒", "乡村振兴")
    assert boards.complete and boards.sectors()[0] == "食品饮料"


def test_noise_only_concepts_are_kept_rather_than_empty() -> None:
    boards = sb.build_boards(["银行"], ["沪股通", "融资融券"])
    assert boards.concepts == ("沪股通", "融资融券")


def test_tencent_plate_orders_industry_by_level_and_handles_missing_plate() -> None:
    boards = sb.parse_tencent_plate(TENCENT_SAMPLE)
    assert boards.industries == ("食品饮料", "白酒Ⅱ")
    assert boards.concepts == ("白酒概念", "国企改革")
    assert sb.parse_tencent_plate({"code": 0, "data": {"plate": None}}).empty
    assert sb.parse_tencent_plate("garbage").empty


def test_merge_fills_only_the_missing_category() -> None:
    first = sb.Boards(industries=("银行",))
    second = sb.Boards(industries=("别的",), concepts=("数字货币",))
    merged = sb.merge(first, second)
    assert merged.industries == ("银行",) and merged.concepts == ("数字货币",)


def test_fuyao_parsers() -> None:
    catalog = {"code": 0, "data": {"item": [{"thscode": "886042.TI", "name": "白酒概念"}, {}]}}
    assert sb.parse_fuyao_catalog(catalog) == [("886042.TI", "白酒概念")]
    members = {"data": {"item": [{"ticker": "600519"}, {"thscode": "000858.SZ"}, {"ticker": "x"}]}}
    assert sb.parse_fuyao_members(members) == ["600519", "000858"]
    assert sb.fuyao_envelope_error({"code": 0}) is None
    assert sb.fuyao_envelope_error({"code": 2003, "message": "bad"}) == ("2003", "bad")
    assert sb.fuyao_headers(_FAKE_KEY)["X-api-key"] == _FAKE_KEY


# ───────────── 回退链 ─────────────


class _Gateway:
    def __init__(self, routes: Mapping[str, object], fail: tuple[str, ...] = ()) -> None:
        self.routes = routes
        self.fail = fail
        self.calls: list[str] = []
        self.headers: list[Mapping[str, str]] = []

    async def get(self, url: str, headers: Mapping[str, str]) -> bytes:
        self.calls.append(url)
        self.headers.append(headers)
        for marker in self.fail:
            if marker in url:
                raise ProviderError(code="UPSTREAM_TIMEOUT", message="上游超时", status_code=504)
        for marker, body in self.routes.items():
            if marker in url:
                return json.dumps(body).encode()
        raise ProviderError(code="NO_ROUTE", message=f"未配置 {url}", status_code=500)

    async def post(self, url: str, body: bytes, headers: Mapping[str, str]) -> bytes:
        return await self.get(url, headers)


def _service(gateway: _Gateway, fuyao_key: str = "") -> ASharePanoramaService:
    now = datetime(2026, 11, 6, 16, 0, tzinfo=SHANGHAI)
    return ASharePanoramaService(
        gateway=gateway, cache=TtlCache(), now=lambda: now, fuyao_api_key=fuyao_key
    )


def _boards(service: ASharePanoramaService, acc: _Acc, code: str = "600519") -> sb.Boards:
    hits, reasons = asyncio.run(service._stock_boards(acc, code))
    return service._finish_boards(acc, hits, reasons, {})


def test_chain_uses_eastmoney_f10_first_and_skips_the_rest_when_complete() -> None:
    gateway = _Gateway({"RPT_F10_CORETHEME_BOARDTYPE": EM_SAMPLE})
    acc = _Acc()
    boards = _boards(_service(gateway), acc)
    assert boards.industries[0] == "食品饮料" and "白酒" in boards.concepts
    assert len(gateway.calls) == 1 and "SECUCODE" in gateway.calls[0]
    assert "600519.SH" in gateway.calls[0]
    assert acc.sources == ["东方财富 F10 所属板块"]
    assert "所属板块" not in acc.unavailable


def test_chain_falls_back_to_tencent_when_eastmoney_fails() -> None:
    gateway = _Gateway({"stockinfo/plate": TENCENT_SAMPLE}, fail=("RPT_F10_CORETHEME_BOARDTYPE",))
    acc = _Acc()
    boards = _boards(_service(gateway), acc)
    assert boards.industries == ("食品饮料", "白酒Ⅱ")
    assert acc.sources == ["腾讯财经 所属板块"]
    assert "sh600519" in gateway.calls[-1]
    assert "所属板块" not in acc.unavailable  # 回退成功就不报失败


def test_chain_merges_missing_category_across_sources() -> None:
    em_only_industry = _f10([_row("银行", "行业")])
    gateway = _Gateway(
        {"RPT_F10_CORETHEME_BOARDTYPE": em_only_industry, "stockinfo/plate": TENCENT_SAMPLE}
    )
    boards = _boards(_service(gateway), _Acc())
    assert boards.industries == ("银行",)  # 行业以东财为准
    assert boards.concepts == ("白酒概念", "国企改革")  # 概念由腾讯补


def test_chain_reports_honest_unavailable_when_everything_is_empty() -> None:
    gateway = _Gateway(
        {"RPT_F10_CORETHEME_BOARDTYPE": _f10([]), "stockinfo/plate": {"data": {"plate": None}}}
    )
    acc = _Acc()
    boards = _boards(_service(gateway), acc, "920001")
    assert boards.empty and acc.sources == []
    reason = acc.unavailable["所属板块"]
    assert "东财F10所属板块" in reason and "腾讯所属板块" in reason
    assert NOT_CONFIGURED in reason  # 没配扶摇 Key 就明说


def test_last_resort_uses_push2_fields_already_fetched_with_the_quote() -> None:
    gateway = _Gateway({}, fail=("RPT_F10", "stockinfo/plate"))
    service = _service(gateway)
    acc = _Acc()
    hits, reasons = asyncio.run(service._stock_boards(acc, "600519"))
    quote = {"f127": "白酒Ⅱ", "f129": "白酒,沪股通,乡村振兴"}
    boards = service._finish_boards(acc, hits, reasons, quote)
    assert boards.industries == ("白酒Ⅱ",) and boards.concepts == ("白酒", "乡村振兴")
    assert "东方财富 行情" in acc.sources and "所属板块" not in acc.unavailable


def test_partial_result_notes_which_category_is_missing() -> None:
    gateway = _Gateway(
        {"RPT_F10_CORETHEME_BOARDTYPE": _f10([_row("银行", "行业")])},
        fail=("stockinfo/plate",),
    )
    acc = _Acc()
    boards = _boards(_service(gateway), acc)
    assert boards.industries == ("银行",) and not boards.concepts
    assert acc.unavailable["所属板块"].startswith("概念板块暂缺")


# ───────────── 扶摇反查索引 ─────────────


def _fuyao_routes() -> dict[str, object]:
    return {
        "tag=cn_concept": {
            "code": 0,
            "data": {"item": [{"thscode": "886001.TI", "name": "机器人"}]},
        },
        "tag=industry": {
            "code": 0,
            "data": {
                "item": [
                    {"thscode": "884001.TI", "name": "种子生产"},
                    {"thscode": "881101.TI", "name": "种植业与林业"},
                ]
            },
        },
        "thscode=886001.TI": {
            "code": 0,
            "data": {"item": [{"ticker": "920001"}, {"ticker": "000001"}]},
        },
        "thscode=881101.TI": {"code": 0, "data": {"item": [{"ticker": "920001"}]}},
        "thscode=884001.TI": {"code": 0, "data": {"item": [{"ticker": "920001"}]}},
    }


def test_fuyao_index_reverse_maps_members_and_sends_key_only_as_header() -> None:
    gateway = _Gateway(_fuyao_routes())
    index = FuyaoBoardIndex(gateway=gateway, api_key=lambda: _FAKE_KEY)
    asyncio.run(index.build())
    boards, reason = asyncio.run(index.lookup("920001"))
    assert reason == "" and boards is not None
    assert boards.industries == ("种植业与林业", "种子生产")  # 881 一级在前
    assert boards.concepts == ("机器人",)
    empty, _ = asyncio.run(index.lookup("600000"))
    assert empty is not None and empty.empty  # 索引里没有这只：如实为空
    assert all(_FAKE_KEY not in url for url in gateway.calls)
    assert all(h.get("X-api-key") == _FAKE_KEY for h in gateway.headers)


def test_fuyao_lookup_skips_without_key_and_reports_building() -> None:
    index = FuyaoBoardIndex(gateway=_Gateway({}), api_key=lambda: "")
    assert asyncio.run(index.lookup("920001")) == (None, NOT_CONFIGURED)

    async def first_lookup() -> tuple[object, str]:
        slow = FuyaoBoardIndex(gateway=_Gateway(_fuyao_routes()), api_key=lambda: _FAKE_KEY)
        result = await slow.lookup("920001")  # 第一次：后台开始构建，当场返回「构建中」
        assert slow._task is not None
        await slow._task
        return result

    boards, reason = asyncio.run(first_lookup())
    assert boards is None and reason == BUILDING


def test_fuyao_revoked_key_is_reported_without_echoing_it() -> None:
    gateway = _Gateway({"ths-index-list": {"code": 2003, "message": "Invalid or revoked API key"}})
    index = FuyaoBoardIndex(gateway=gateway, api_key=lambda: _FAKE_KEY)

    async def run() -> str:
        await index.lookup("920001")
        assert index._task is not None
        await index._task
        return (await index.lookup("920001"))[1]

    message = asyncio.run(run())
    assert "无效或已撤销" in message and _FAKE_KEY not in message


def test_fuyao_build_fails_when_too_many_boards_error() -> None:
    routes = _fuyao_routes()
    gateway = _Gateway(routes, fail=("thscode=886001.TI", "thscode=881101.TI"))
    index = FuyaoBoardIndex(gateway=gateway, api_key=lambda: _FAKE_KEY)
    try:
        asyncio.run(index.build())
    except ProviderError as error:
        assert error.code == "UPSTREAM_PARTIAL"
    else:  # pragma: no cover
        raise AssertionError("应该因失败比例过高而构建失败")
    assert index._index is None
