"""个股所属板块（行业 + 概念）的数据源适配器：只构造 URL / 请求头并解析，不发请求。

口径（2026-11 在阿里云 ECS 上实测，全部免登录、免费）：
- 东财 F10 核心题材 `RPT_F10_CORETHEME_BOARDTYPE`（datacenter 域，ECS 可达）：一次返回该股全部板块，
  `BOARD_TYPE` 为「行业」/「板块」（地域）/ 空（概念与各类成分标签）；`IS_PRECISE=1` 是核心题材。
  沪深京三个市场都有，是主源。
- 腾讯 `ifzqgtimg/appstock/app/stockinfo/plate`：
  `concept`（概念）、`plate`（申万行业，level 1/2）、`area`（地域）。沪深有、北交所没有，当第二源。
- 同花顺扶摇（需 `FUYAO_API_KEY`）：只有「板块 → 成分股」，没有「个股 → 板块」，
  所以由服务层把概念 / 行业板块的成分全量拉一遍反查成映射，当最后的兜底。
TickFlow 只有申万行业 universe 且成分数明显不全（如 SW1 基础化工只有 11 只），无概念，没有接入。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

_EM_F10_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_TENCENT_PLATE_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/stockinfo/plate"
FUYAO_BASE = "https://fuyao.aicubes.cn"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
EM_F10_HEADERS: Mapping[str, str] = {
    "User-Agent": _UA,
    "Referer": "https://emweb.securities.eastmoney.com/",
}
TENCENT_PLATE_HEADERS: Mapping[str, str] = {
    "User-Agent": _UA,
    "Referer": "https://gu.qq.com/",
}

MAX_INDUSTRIES = 3
MAX_CONCEPTS = 12

# 各类「指数成分 / 市场标签 / 业绩与资金状态」：不是产业题材，展示出来只会淹没真正的概念。
_NOISE = re.compile(
    r"(_$|^昨日|^今日|中报|年报|季报|预[增减盈亏]|增仓|减仓|重仓|热股|^MSCI|富时|标准普尔|标普|"
    r"沪股通|深股通|融资融券|转融券|^上证\d|^深证\d|^HS300|^央视50|创业板综|科创板|"
    r"大盘|中盘|小盘|成长$|价值$|成长股|价值股|风格$|权重股|破净|百元股|高价股|低价股|高市盈率|"
    r"趋势股|连续亏损|题材股|茅指数|AH股|北交所概念)"
)


@dataclass(frozen=True)
class Boards:
    """一只股票的所属板块，已去重、去噪、限量。"""

    industries: tuple[str, ...] = ()
    concepts: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.industries and not self.concepts

    @property
    def complete(self) -> bool:
        return bool(self.industries) and bool(self.concepts)

    def sectors(self) -> list[str]:
        """行业在前、概念在后的展示顺序。"""

        return [*self.industries, *self.concepts]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _industry_level(name: str) -> int:
    """申万 / 东财行业名带 Ⅱ / Ⅲ 后缀表示二、三级；一级没有后缀。"""

    if name.endswith("Ⅲ"):
        return 2
    if name.endswith("Ⅱ"):
        return 1
    return 0


def _unique(names: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(name for name in names if name))


def build_boards(industries: Iterable[str], concepts: Iterable[str]) -> Boards:
    """去重、行业按层级排序、概念去噪（全是噪声时保留原样，宁可多也不空）。"""

    inds = sorted(_unique(industries), key=_industry_level)[:MAX_INDUSTRIES]
    raw = [name for name in _unique(concepts) if name not in inds]
    kept = [name for name in raw if not _NOISE.search(name)]
    return Boards(tuple(inds), tuple((kept or raw)[:MAX_CONCEPTS]))


def merge(first: Boards, second: Boards) -> Boards:
    """第一源缺哪一类就用第二源补哪一类，不混合同类名称。"""

    return Boards(first.industries or second.industries, first.concepts or second.concepts)


# ───────────── 东财 F10 ─────────────


def em_f10_url(secucode: str) -> str:
    query = urlencode(
        {
            "reportName": "RPT_F10_CORETHEME_BOARDTYPE",
            "columns": "ALL",
            "filter": f'(SECUCODE="{secucode}")',
            "pageNumber": "1",
            "pageSize": "300",
            "source": "HSF10",
            "client": "PC",
        }
    )
    return f"{_EM_F10_URL}?{query}"


def _rows(doc: object, *path: str) -> list[dict[str, Any]]:
    node: Any = doc
    for key in path:
        node = node.get(key) if isinstance(node, dict) else None
    return [row for row in node if isinstance(row, dict)] if isinstance(node, list) else []


def parse_em_f10(doc: object) -> Boards:
    rows = _rows(doc, "result", "data")
    industries: list[str] = []
    precise: list[str] = []
    others: list[str] = []
    for row in rows:
        name = _text(row.get("BOARD_NAME"))
        kind = _text(row.get("BOARD_TYPE"))
        if not name or kind == "板块":  # 地域板块不算行业 / 概念
            continue
        if kind == "行业":
            industries.append(name)
        elif str(row.get("IS_PRECISE")) == "1":
            precise.append(name)
        else:
            others.append(name)
    return build_boards(industries, [*precise, *others])


# ───────────── 腾讯 plate ─────────────


def tencent_plate_url(tcode: str) -> str:
    return f"{_TENCENT_PLATE_URL}?{urlencode({'code': tcode})}"


def _level(row: Mapping[str, Any]) -> int:
    try:
        return int(str(row.get("level")))
    except ValueError:
        return 9


def parse_tencent_plate(doc: object) -> Boards:
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, dict):
        return Boards()
    plates = sorted(_rows(data, "plate"), key=_level)
    industries = [_text(row.get("name")) for row in plates]
    concepts = [_text(row.get("name")) for row in _rows(data, "concept")]
    return build_boards(industries, concepts)


# ───────────── 同花顺扶摇（板块成分 → 反查） ─────────────


def fuyao_headers(api_key: str) -> dict[str, str]:
    return {"X-api-key": api_key, "User-Agent": "FinScope-AI/1.0"}


def fuyao_catalog_url(tag: str) -> str:
    """tag：`cn_concept`（A 股概念）/ `industry`（同花顺行业）。"""

    return f"{FUYAO_BASE}/api/a-share-index/catalog/ths-index-list?{urlencode({'tag': tag})}"


def fuyao_members_url(thscode: str) -> str:
    return (
        f"{FUYAO_BASE}/api/a-share-index/constituents/ths-stock-list?"
        f"{urlencode({'thscode': thscode})}"
    )


def fuyao_envelope_error(doc: object) -> tuple[str, str] | None:
    """信封 code 非 0 时返回 (code, message)；成功返回 None。不含任何密钥。"""

    if not isinstance(doc, dict):
        return "bad_response", "扶摇响应不是 JSON 对象"
    code = doc.get("code")
    if code in (0, "0"):
        return None
    return str(code), _text(doc.get("message")) or "扶摇接口错误"


def parse_fuyao_catalog(doc: object) -> list[tuple[str, str]]:
    """[(thscode, 名称)]。"""

    out: list[tuple[str, str]] = []
    for row in _rows(doc, "data", "item"):
        thscode, name = _text(row.get("thscode")), _text(row.get("name"))
        if thscode and name:
            out.append((thscode, name))
    return out


def parse_fuyao_members(doc: object) -> list[str]:
    """成分股 6 位代码列表。"""

    out: list[str] = []
    for row in _rows(doc, "data", "item"):
        ticker = _text(row.get("ticker")) or _text(row.get("thscode")).split(".")[0]
        if len(ticker) == 6 and ticker.isdigit():
            out.append(ticker)
    return out
